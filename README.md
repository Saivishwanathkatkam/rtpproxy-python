# Simple RTPProxy (Python)

A lightweight RTP proxy implementation in Python designed to replace the traditional rtpproxy server. This proxy is used in conjunction with **OpenSIPS** to handle RTP/RTCP packet forwarding for VoIP calls while SIP signaling is managed by OpenSIPS.

## Overview

**Architecture:**
- **OpenSIPS** — handles SIP signaling (INVITE, BYE, etc.)
- **Python RTPProxy** — handles RTP/RTCP media forwarding via the rtpproxy module
- **User A ↔ Python Proxy ↔ User B** — media path

When a SIP call is initiated through OpenSIPS, the rtpproxy module sends control commands to this Python proxy to allocate ports and establish RTP forwarding between the two call endpoints.

## Key Features

- **Port Allocation** — dynamically allocates even/odd RTP/RTCP port pairs
- **NAT Learning** — learns real peer addresses from first packet (handles NAT scenarios)
- **Media Forwarding** — transparently forwards RTP/RTCP packets between call sides
- **Session Management** — tracks active sessions and cleans up resources on call termination
- **Control Protocol** — implements rtpproxy-compatible UDP control messages

## Prerequisites

- Python 3.6+
- OpenSIPS with rtpproxy module compiled/enabled

## Running

```sh
python3 rtpproxy.py
```

**Default Configuration:**
- Control protocol listens on: `127.0.0.1:7722`
- RTP port range: `10000–20000`
- Session timeout: `60` seconds

To modify these values, edit the configuration section at the top of `rtpproxy.py`:

```python
CONTROL_HOST = '127.0.0.1'
CONTROL_PORT = 7722
RTP_HOST = '0.0.0.0'
MIN_RTP_PORT = 10000
MAX_RTP_PORT = 20000
SESSION_TIMEOUT = 60
```

## OpenSIPS Integration

In your OpenSIPS configuration file (e.g., `opensips.cfg`), load the rtpproxy module and point it to this proxy:

```
loadmodule "rtpproxy.so"

modparam("rtpproxy", "rtpproxy_sock", "udp:127.0.0.1:7722")
```

Then, in your routing logic, use the rtpproxy functions:

```
# On INVITE (offer)
rtpproxy_offer("co");

# On 200 OK (answer)
rtpproxy_answer("co");

# On BYE (delete)
rtpproxy_unforce();
```

### Monitor logs

The proxy prints detailed debug information for each command, session, and packet:

```
[CONTROL] Listening on 127.0.0.1:7722
[OFFER] Call-ID: CALLID-abc, From-tag: aTag
[PORT] Allocated port pair: 10000/10001
[RTP] Received 160 bytes on port 10000 from 192.168.1.10:4000
[RTP] Forwarded successfully
```
### Complete mapping in rtpproxy:
```
Port 10000: Receives from Callee → Forwards to Caller (192.168.0.167:12052)
Port 10002: Receives from Caller → Forwards to Callee (192.168.0.158:40828)
```

**Caller → Callee:**
```
1. Caller sends RTP packet to 192.168.0.167:10002
   ↓
2. RTPProxy receives on port 10002
   ↓
3. Looks up: port_to_session[10002] → finds session, side='A' (caller side)
   ↓
4. Since packet came from caller's port, destination = side_b (callee)
   ↓
5. Forwards packet to: session.side_b.remote_ip:remote_port
                        192.168.0.158:40828
   ↓
6. Callee receives packet!
```

**Callee → Caller:**
```
1. Callee sends RTP packet to 192.168.0.167:10000
   ↓
2. RTPProxy receives on port 10000
   ↓
3. Looks up: port_to_session[10000] → finds session, side='B' (callee side)
   ↓
4. Since packet came from callee's port, destination = side_a (caller)
   ↓
5. Forwards packet to: session.side_a.remote_ip:remote_port
                        192.168.0.167:12052
   ↓
6. Caller receives packet!

### Architecture Diagram

```
Caller (192.168.x.x:12052)
    ↓ sends to 10002
    ↓
┌───────────────────────────────┐
│   RTPProxy (192.168.x.x)    │
│                               │
│  Port 10002: Caller's side    │───→ Forwards to Callee
│    Mapping: → 192.168.x.y:  │     (40828)
│                40828           │
│                               │
│  Port 10000: Callee's side    │───→ Forwards to Caller
│    Mapping: → 192.168.x.x:  │     (12052)
│                12052           │
└───────────────────────────────┘
    ↑
    ↑ sends to 10000
Callee (192.168.x.y:40828)

## Limitations

- **Single-process** — not designed for horizontal scaling
- **No authentication** — assumes trusted control messages from OpenSIPS
- **Educational prototype** — minimal error recovery
- **No TLS** — control protocol is plaintext UDP

## Known Issues / TODOs

- Incomplete command handlers in `handle_control_message()` (marked with `...`)
- No session cleanup on timeout (SESSION_TIMEOUT is defined but not enforced)
- No detailed packet statistics or RTP header inspection


## References

- [rtpproxy protocol](https://rtpproxy.org/command.html)
-
