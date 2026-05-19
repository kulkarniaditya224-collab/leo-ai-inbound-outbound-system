import asyncio
import json
import logging
import os
import ssl
import certifi
from typing import Optional

from dotenv import load_dotenv

# Patch SSL before any network import
_orig_ssl = ssl.create_default_context
def _certifi_ssl(purpose=ssl.Purpose.SERVER_AUTH, **kwargs):
    if not kwargs.get("cafile") and not kwargs.get("capath") and not kwargs.get("cadata"):
        kwargs["cafile"] = certifi.where()
    return _orig_ssl(purpose, **kwargs)
ssl.create_default_context = _certifi_ssl

from livekit import agents, api, rtc
from livekit.agents import Agent, AgentSession, RoomInputOptions
try:
    from livekit.agents import RoomOptions as _RoomOptions
    _HAS_ROOM_OPTIONS = True
except ImportError:
    _HAS_ROOM_OPTIONS = False
from livekit.plugins import noise_cancellation, silero

from db import init_db, log_error, get_enabled_tools, get_inbound_route_by_trunk, increment_inbound_route_calls, get_agent_profile
from prompts import build_prompt
from tools import LeoTools

load_dotenv(".env", override=False)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("leo-agent")

SIP_DOMAIN = os.getenv("VOBIZ_SIP_DOMAIN", "")


async def _log(level: str, msg: str, detail: str = "") -> None:
    if level == "info":      logger.info(msg)
    elif level == "warning": logger.warning(msg)
    else:                    logger.error(msg)
    try:
        await log_error("agent", msg, detail, level)
    except Exception:
        pass


def load_db_settings_to_env() -> None:
    """Load Supabase settings into os.environ as FALLBACK only.
    VPS env vars are the single source of truth — they are NEVER overwritten.
    Supabase values only fill in keys that are not already set on the VPS."""
    url = os.getenv("SUPABASE_URL", "")
    key = os.getenv("SUPABASE_SERVICE_KEY", "")
    if not url or not key:
        return
    try:
        from supabase import create_client
        client = create_client(url, key)
        result = client.table("settings").select("key, value").execute()
        loaded = 0
        for row in (result.data or []):
            k, v = row.get("key", ""), row.get("value", "")
            if v and not os.environ.get(k):
                os.environ[k] = v
                loaded += 1
        logger.info("Loaded %d fallback settings from Supabase (VPS env vars preserved)", loaded)
    except Exception as exc:
        logger.warning("Could not load settings from Supabase: %s", exc)


# ── Import Google plugin paths ───────────────────────────────────────────────
_google_realtime = None
_google_beta_realtime = None
_google_llm = None
_google_tts = None

try:
    from livekit.plugins import google as _gp
    try:
        _google_realtime = _gp.realtime.RealtimeModel
        logger.info("Loaded google.realtime.RealtimeModel (stable path)")
    except AttributeError:
        pass
    try:
        _google_beta_realtime = _gp.beta.realtime.RealtimeModel
        logger.info("Loaded google.beta.realtime.RealtimeModel (beta path)")
    except AttributeError:
        pass
    try:
        _google_llm = _gp.LLM
        _google_tts = _gp.TTS
    except AttributeError:
        pass
except ImportError:
    logger.warning("livekit-plugins-google not installed")

_deepgram_stt = None
try:
    from livekit.plugins import deepgram as _dg
    _deepgram_stt = _dg.STT
except ImportError:
    pass


# ── Session factory ──────────────────────────────────────────────────────────

def _build_session(tools: list, system_prompt: str) -> AgentSession:
    """
    Build AgentSession with Gemini Live or pipeline fallback.

    CRITICAL SILENCE-PREVENTION CONFIG — all 3 required:
    1. SessionResumptionConfig(transparent=True) — auto-reconnects on timeout
    2. ContextWindowCompressionConfig — prevents freeze when context fills
    3. RealtimeInputConfig with VAD tuning — 2s silence threshold, low sensitivity
    """
    use_realtime = os.getenv("USE_GEMINI_REALTIME", "true").lower() == "true"
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
    gemini_voice = os.getenv("GEMINI_TTS_VOICE", "Aoede")
    api_key = os.getenv("GOOGLE_API_KEY", "")

    if use_realtime and (_google_realtime or _google_beta_realtime) and api_key:
        logger.info(f"Building Gemini Live session: model={gemini_model}, voice={gemini_voice}")
        try:
            from google.genai import types as _gt

            _live_kwargs = dict(
                model=gemini_model,
                api_key=api_key,
                voice=gemini_voice,
                instructions=system_prompt,
                # 1. Transparent session resumption
                session_resumption=_gt.SessionResumptionConfig(transparent=True),
                # 2. Context window compression
                context_window_compression=_gt.ContextWindowCompressionConfig(
                    trigger_tokens=25600,
                    sliding_window=_gt.SlidingWindow(target_tokens=12800),
                ),
                # 3. VAD tuning — MUST use full enum string
                realtime_input_config=_gt.RealtimeInputConfig(
                    automatic_activity_detection=_gt.AutomaticActivityDetection(
                        end_of_speech_sensitivity=_gt.EndSensitivity.END_SENSITIVITY_LOW,
                        silence_duration_ms=2000,
                        prefix_padding_ms=200,
                    ),
                ),
            )

            RealtimeClass = _google_realtime or _google_beta_realtime
            realtime_model = RealtimeClass(**_live_kwargs)
            return AgentSession(llm=realtime_model)

        except Exception as exc:
            logger.error(f"Gemini Live init failed: {exc} — falling back to pipeline")

    # ── Pipeline fallback (STT + LLM + TTS) ──────────────────────────────────
    logger.info("Building pipeline session (STT + LLM + TTS)")
    _dg_key = os.getenv("DEEPGRAM_API_KEY", "")
    stt = _deepgram_stt() if (_deepgram_stt and _dg_key) else None
    llm_inst = _google_llm(model=gemini_model, api_key=api_key) if _google_llm and api_key else None
    tts = None
    return AgentSession(
        vad=silero.VAD.load(),
        stt=stt,
        llm=llm_inst,
        tts=tts,
    )


class LeoAssistant(Agent):
    """LEO AI voice assistant — passes tools=[] to avoid duplicate tool error."""
    def __init__(self, instructions: str) -> None:
        super().__init__(instructions=instructions, tools=[])


async def entrypoint(ctx: agents.JobContext):
    """
    Unified entrypoint for LEO AI — handles both inbound and outbound calls.

    MODE DETECTION:
    - If metadata contains "phone_number" → OUTBOUND (we dial out)
    - If metadata contains "direction": "inbound" → INBOUND (caller dialed in)
    - If SIP participant already in room → INBOUND
    - Default: OUTBOUND

    OUTBOUND FLOW:
    1. Parse metadata for phone_number, lead_name, prompts, overrides.
    2. Connect to room.
    3. Dial via SIP (wait_until_answered=True) — DIAL-FIRST PATTERN.
    4. Build and start Gemini Live session AFTER call is answered.

    INBOUND FLOW:
    1. Caller already connected via SIP inbound trunk.
    2. Detect inbound route from trunk_id.
    3. Apply route-specific profile/prompt/greeting.
    4. Start Gemini Live session immediately.
    """
    logger.info(f"Agent entrypoint — room: {ctx.room.name}")

    # ── Parse metadata ───────────────────────────────────────────────────────
    phone_number = None
    lead_name = "there"
    business_name = "our company"
    service_type = "our service"
    custom_prompt = None
    voice_override = None
    model_override = None
    tools_override = None
    direction = "outbound"
    inbound_trunk_id = None
    agent_profile_id = None

    for meta_src in [ctx.job.metadata, getattr(ctx.room, "metadata", None)]:
        if not meta_src:
            continue
        try:
            data = json.loads(meta_src)
            phone_number = data.get("phone_number") or phone_number
            lead_name = data.get("lead_name") or lead_name
            business_name = data.get("business_name") or business_name
            service_type = data.get("service_type") or service_type
            custom_prompt = data.get("system_prompt") or custom_prompt
            voice_override = data.get("voice_override") or voice_override
            model_override = data.get("model_override") or model_override
            tools_override = data.get("tools_override") or tools_override
            agent_profile_id = data.get("agent_profile_id") or agent_profile_id
            if data.get("direction") == "inbound":
                direction = "inbound"
            if data.get("inbound_trunk_id"):
                inbound_trunk_id = data["inbound_trunk_id"]
                direction = "inbound"
        except Exception:
            pass

    # ── Detect inbound from SIP participants already in room ─────────────────
    await ctx.connect()
    for p in ctx.room.remote_participants.values():
        if p.identity and p.identity.startswith("sip_"):
            if not phone_number:
                phone_number = p.identity.replace("sip_", "")
            if direction != "inbound":
                direction = "inbound"
            break

    # ── Inbound route lookup ─────────────────────────────────────────────────
    inbound_route = None
    if direction == "inbound" and inbound_trunk_id:
        try:
            inbound_route = await get_inbound_route_by_trunk(inbound_trunk_id)
            if inbound_route:
                await increment_inbound_route_calls(inbound_route["id"])
                if not custom_prompt and inbound_route.get("system_prompt"):
                    custom_prompt = inbound_route["system_prompt"]
                if not agent_profile_id and inbound_route.get("agent_profile_id"):
                    agent_profile_id = inbound_route["agent_profile_id"]
        except Exception as exc:
            await _log("warning", f"Inbound route lookup failed: {exc}")

    # ── Agent profile overrides ──────────────────────────────────────────────
    if agent_profile_id:
        try:
            profile = await get_agent_profile(agent_profile_id)
            if profile:
                if not custom_prompt and profile.get("system_prompt"):
                    custom_prompt = profile["system_prompt"]
                if not voice_override and profile.get("voice"):
                    voice_override = profile["voice"]
                if not model_override and profile.get("model"):
                    model_override = profile["model"]
                if not tools_override and profile.get("enabled_tools"):
                    tools_override = profile["enabled_tools"]
        except Exception:
            pass

    # Apply overrides to env before building session
    if voice_override:
        os.environ["GEMINI_TTS_VOICE"] = voice_override
    if model_override:
        os.environ["GEMINI_MODEL"] = model_override

    # Parse enabled tools
    enabled_tools = []
    if tools_override:
        try:
            parsed = json.loads(tools_override) if isinstance(tools_override, str) else tools_override
            if isinstance(parsed, list):
                enabled_tools = parsed
        except Exception:
            pass

    # Build system prompt
    system_prompt = build_prompt(
        mode=direction,
        lead_name=lead_name,
        business_name=business_name,
        service_type=service_type,
        custom_prompt=custom_prompt,
    )

    # Initialize tool context
    tool_ctx = LeoTools(ctx, phone_number=phone_number, lead_name=lead_name, direction=direction)
    tool_ctx.agent_profile_id = agent_profile_id

    await _log("info", f"[{direction.upper()}] Connected to room: {ctx.room.name} | phone={phone_number}")

    # ── OUTBOUND: Dial — MUST come before session.start() ────────────────────
    if direction == "outbound" and phone_number:
        trunk_id = os.getenv("OUTBOUND_TRUNK_ID")
        if not trunk_id:
            await _log("error", "OUTBOUND_TRUNK_ID not set — cannot place outbound call")
            ctx.shutdown()
            return
        await _log("info", f"Dialing {phone_number} via SIP trunk {trunk_id}")
        try:
            await ctx.api.sip.create_sip_participant(
                api.CreateSIPParticipantRequest(
                    room_name=ctx.room.name,
                    sip_trunk_id=trunk_id,
                    sip_call_to=phone_number,
                    participant_identity=f"sip_{phone_number}",
                    wait_until_answered=True,
                )
            )
        except Exception as exc:
            await _log("error", f"SIP dial FAILED for {phone_number}: {exc}")
            ctx.shutdown()
            return
        await _log("info", f"Call ANSWERED — {phone_number} picked up")

    # ── Build and start Gemini Live ──────────────────────────────────────────
    gemini_model = os.getenv("GEMINI_MODEL", "gemini-3.1-flash-live-preview")
    await _log("info", f"Building AI session — model={gemini_model}, direction={direction}")
    active_tools = tool_ctx.build_tool_list(enabled_tools)
    await _log("info", f"Tools loaded: {[t.__name__ for t in active_tools]}")
    session = _build_session(tools=active_tools, system_prompt=system_prompt)

    # Use RoomOptions if available (non-deprecated), else fall back
    # NEVER use close_on_disconnect=True with SIP — drops on any audio blip
    if _HAS_ROOM_OPTIONS:
        from livekit.agents import RoomOptions as _RO
        _session_kwargs = dict(
            room=ctx.room,
            agent=LeoAssistant(instructions=system_prompt),
            room_options=_RO(input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony())),
        )
    else:
        _session_kwargs = dict(
            room=ctx.room,
            agent=LeoAssistant(instructions=system_prompt),
            room_input_options=RoomInputOptions(noise_cancellation=noise_cancellation.BVCTelephony()),
        )

    await session.start(**_session_kwargs)
    await _log("info", f"[{direction.upper()}] Agent session started — AI ready")

    # ── Optional S3 recording ────────────────────────────────────────────────
    if phone_number:
        _aws_key    = os.getenv("S3_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY_ID", "")
        _aws_secret = os.getenv("S3_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_ACCESS_KEY", "")
        _aws_bucket = os.getenv("S3_BUCKET") or os.getenv("AWS_BUCKET_NAME", "")
        _s3_endpoint = os.getenv("S3_ENDPOINT_URL") or os.getenv("S3_ENDPOINT", "")
        _s3_region  = os.getenv("S3_REGION") or os.getenv("AWS_REGION", "ap-northeast-1")
        if _aws_key and _aws_secret and _aws_bucket:
            try:
                _recording_path = f"recordings/{ctx.room.name}.ogg"
                _egress_req = api.RoomCompositeEgressRequest(
                    room_name=ctx.room.name, audio_only=True,
                    file_outputs=[api.EncodedFileOutput(
                        file_type=api.EncodedFileType.OGG, filepath=_recording_path,
                        s3=api.S3Upload(access_key=_aws_key, secret=_aws_secret,
                                        bucket=_aws_bucket, region=_s3_region, endpoint=_s3_endpoint),
                    )],
                )
                _egress = await ctx.api.egress.start_room_composite_egress(_egress_req)
                _s3_ep = _s3_endpoint.rstrip("/")
                tool_ctx.recording_url = (f"{_s3_ep}/{_aws_bucket}/{_recording_path}"
                                           if _s3_ep else f"s3://{_aws_bucket}/{_recording_path}")
                await _log("info", f"Recording started: egress={_egress.egress_id}")
            except Exception as _exc:
                await _log("warning", f"Recording start failed (non-fatal): {_exc}")

    # ── Greeting ─────────────────────────────────────────────────────────────
    _active_model = os.getenv("GEMINI_MODEL", "")
    if "3.1" in _active_model or "2.5" in _active_model:
        await _log("info", "Gemini native-audio: model will greet autonomously from system prompt")
    else:
        if direction == "inbound":
            greeting = f"A caller just connected. Welcome them warmly to {business_name} and ask how you can help."
        else:
            greeting = (
                f"The call just connected. Greet the lead and ask if you're speaking with {lead_name}."
                if phone_number else "Greet the caller warmly."
            )
        try:
            await session.generate_reply(instructions=greeting)
        except Exception as _gr_exc:
            await _log("warning", f"generate_reply failed: {_gr_exc}")

    # ── Keep session alive until SIP participant actually leaves ─────────────
    _sip_identity = f"sip_{phone_number}" if phone_number else None
    _disconnect_event = asyncio.Event()

    def _on_participant_disconnected(participant: rtc.RemoteParticipant):
        if _sip_identity and participant.identity == _sip_identity:
            _disconnect_event.set()
        elif not _sip_identity:
            _disconnect_event.set()
    def _on_disconnected():
        _disconnect_event.set()

    ctx.room.on("participant_disconnected", _on_participant_disconnected)
    ctx.room.on("disconnected", _on_disconnected)

    try:
        await asyncio.wait_for(_disconnect_event.wait(), timeout=3600)
    except asyncio.TimeoutError:
        await _log("warning", "Call reached 1-hour safety timeout — shutting down")

    await _log("info", f"[{direction.upper()}] Call ended for {phone_number or 'unknown'}")
    await session.aclose()


if __name__ == "__main__":
    init_db()
    load_db_settings_to_env()
    agents.cli.run_app(
        agents.WorkerOptions(entrypoint_fnc=entrypoint, agent_name="leo-ai")
    )
