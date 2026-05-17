# LEO AI — Voice Intelligence Platform

Production-grade inbound + outbound AI voice calling platform powered by Google Gemini Live, LiveKit Agents, and Vobiz SIP telephony.

## Features

### Outbound
- **Gemini Live Realtime Voice** — Sub-100ms latency AI conversations
- **SIP Outbound Dialing** — Dial-first pattern via Vobiz SIP trunk
- **Campaign Engine** — Mass call campaigns (once / daily / weekdays)
- **Appointment Booking** — AI books appointments + Cal.com sync
- **Contact Memory** — AI remembers past interactions per lead
- **SMS Confirmation** — Twilio-powered booking confirmations

### Inbound
- **SIP Inbound Trunks** — Auto-dispatch agent when callers dial in
- **Inbound Routes** — Per-number routing with custom prompts/profiles
- **Knowledge Base / FAQ** — AI answers from configurable FAQ entries
- **Department Routing** — SIP REFER transfer to human agents
- **Call Direction Tracking** — All logs tagged inbound/outbound

### Shared
- **Agent Profiles** — Multiple AI personalities (inbound/outbound/both)
- **S3 Call Recording** — Optional recording to any S3-compatible storage
- **Premium Dashboard** — Dark-themed web UI with analytics, CRM, logs
- **VPS Env Vars** — Single source of truth for all configuration

## Architecture

```
┌─────────────────────────────────────────────────┐
│  Dashboard (ui/index.html)                      │
│  Premium dark UI — vanilla HTML/CSS/JS          │
└────────────────────┬────────────────────────────┘
                     │ REST API
┌────────────────────▼────────────────────────────┐
│  server.py — FastAPI + APScheduler              │
│  Inbound routes, outbound dispatch, campaigns   │
└────────────────────┬────────────────────────────┘
                     │ LiveKit API
┌────────────────────▼────────────────────────────┐
│  agent.py — Unified LiveKit Agent Worker        │
│  Mode detection → Gemini Live → Tools           │
└────────────────────┬────────────────────────────┘
                     │
    ┌────────────────┼────────────────┐
    ▼                ▼                ▼
 Supabase       Vobiz SIP        Google Gemini
 (Postgres)     (Telephony)      (Live Audio AI)
```

## Files

| File | Purpose |
|------|---------|
| `agent.py` | Unified agent — detects inbound vs outbound, Gemini Live |
| `server.py` | FastAPI backend — inbound routes, outbound dispatch, campaigns, FAQ |
| `db.py` | Supabase operations with direction support |
| `tools.py` | 10 LLM tools (booking, transfer, SMS, FAQ lookup, memory) |
| `prompts.py` | Separate detailed prompts for inbound & outbound |
| `ui/index.html` | Premium dark dashboard |
| `supabase_schema.sql` | Database schema with inbound routes, FAQ, direction fields |
| `Dockerfile` | Production Docker image |
| `start.sh` | Entrypoint script |
| `requirements.txt` | Python dependencies |

## Deployment

### Environment Variables (Single Source of Truth)

Set on your VPS (Coolify/Docker). No `.env` file needed in production.

**Required:**
```
LIVEKIT_URL=wss://your-livekit.example.com
LIVEKIT_API_KEY=APIxxxxxxxx
LIVEKIT_API_SECRET=xxxxxxxx
GOOGLE_API_KEY=AIza...
SUPABASE_URL=https://xxx.supabase.co
SUPABASE_SERVICE_KEY=eyJ...
VOBIZ_SIP_DOMAIN=sip.vobiz.com
VOBIZ_USERNAME=your_user
VOBIZ_PASSWORD=your_pass
VOBIZ_OUTBOUND_NUMBER=+91XXXXXXXXXX
```

**Optional:**
```
GEMINI_MODEL=gemini-3.1-flash-live-preview
GEMINI_TTS_VOICE=Aoede
DEFAULT_TRANSFER_NUMBER=+91XXXXXXXXXX
OUTBOUND_TRUNK_ID=ST_xxx
TWILIO_ACCOUNT_SID=ACxxx
TWILIO_AUTH_TOKEN=xxx
TWILIO_FROM_NUMBER=+1xxx
CALCOM_API_KEY=cal_live_xxx
CALCOM_EVENT_TYPE_ID=12345
S3_ACCESS_KEY_ID=xxx
S3_SECRET_ACCESS_KEY=xxx
S3_BUCKET=recordings
DEEPGRAM_API_KEY=xxx
```

### Steps

1. Run `supabase_schema.sql` in Supabase SQL Editor
2. Deploy:
   ```bash
   docker build -t leo-ai .
   docker run -d --name leo-ai -p 8000:8000 \
     -e LIVEKIT_URL=wss://... \
     -e GOOGLE_API_KEY=... \
     -e SUPABASE_URL=... \
     -e SUPABASE_SERVICE_KEY=... \
     -e VOBIZ_SIP_DOMAIN=... \
     -e VOBIZ_USERNAME=... \
     -e VOBIZ_PASSWORD=... \
     -e VOBIZ_OUTBOUND_NUMBER=... \
     leo-ai
   ```
3. Open dashboard at `http://your-vps:8000`
4. Create outbound trunk via Settings
5. Create inbound routes via Inbound Routes tab
6. Add FAQ entries to Knowledge Base
7. Test calls

## Env Var Priority

```
VPS Environment Variables  →  Supabase settings table  →  Hardcoded defaults
```

## Key Architecture Rules

- **Dial-first pattern** for outbound — SIP call placed BEFORE AI session
- **Auto-dispatch** for inbound — agent joins when SIP participant detected
- **Never use `close_on_disconnect=True`** with SIP
- **Never call `generate_reply()` for Gemini 3.1/2.5** native audio models
- **VAD: `END_SENSITIVITY_LOW`** with 2s silence threshold
- **Session resumption** — transparent reconnection
- **Context compression** — sliding window at 25,600 tokens

## License

Private — All rights reserved.
