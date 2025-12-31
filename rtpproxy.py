#!/usr/bin/env python3
"""
Simple RTPProxy Service - Educational Implementation
Compatible with Python 3.6+
Demonstrates core RTPProxy concepts: control protocol, port allocation, and RTP forwarding
"""

import asyncio
import socket
import time
from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

# Configuration
CONTROL_HOST = '127.0.0.1'
CONTROL_PORT = 7722
RTP_HOST = '0.0.0.0'  # Listen on all interfaces
MIN_RTP_PORT = 10000
MAX_RTP_PORT = 20000
SESSION_TIMEOUT = 60  # seconds


@dataclass
class SessionSide:
    """Represents one side of an RTP session (caller or callee)"""
    proxy_rtp_port: int = 0
    proxy_rtcp_port: int = 0
    remote_ip: str = ""
    remote_port: int = 0
    real_ip: str = ""  # Learned from first packet (for NAT)
    real_port: int = 0
    last_packet_time: float = 0.0


@dataclass
class RTPSession:
    """Complete RTP session with both sides"""
    call_id: str
    from_tag: str
    to_tag: str = ""
    side_a: SessionSide = field(default_factory=SessionSide)  # Caller
    side_b: SessionSide = field(default_factory=SessionSide)  # Callee
    created: float = field(default_factory=time.time)
    state: str = "OFFERING"  # OFFERING, ESTABLISHED


class PortAllocator:
    """Manages RTP/RTCP port allocation"""
    
    def __init__(self, min_port: int, max_port: int):
        self.min_port = min_port
        self.max_port = max_port
        self.allocated_ports = set()
    
    def allocate_pair(self) -> Optional[int]:
        """Allocate a pair of ports (RTP=even, RTCP=odd)"""
        for port in range(self.min_port, self.max_port, 2):
            if port not in self.allocated_ports and (port + 1) not in self.allocated_ports:
                self.allocated_ports.add(port)
                self.allocated_ports.add(port + 1)
                print(f"[PORT] Allocated port pair: {port}/{port+1}")
                return port
        return None
    
    def free_pair(self, rtp_port: int):
        """Free a port pair"""
        if rtp_port in self.allocated_ports:
            self.allocated_ports.discard(rtp_port)
            self.allocated_ports.discard(rtp_port + 1)
            print(f"[PORT] Freed port pair: {rtp_port}/{rtp_port+1}")


class RTPProtocol(asyncio.DatagramProtocol):
    """UDP Protocol for RTP packet forwarding"""
    
    def __init__(self, port: int, proxy):
        self.port = port
        self.proxy = proxy
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        """Called when UDP packet is received"""
        try:
            # Find session
            session_info = self.proxy.port_to_session.get(self.port)
            if not session_info:
                return
            
            session, side = session_info
            packet_type = "RTP" if self.port % 2 == 0 else "RTCP"
            
            print(f"[{packet_type}] Received {len(data)} bytes on port {self.port} from {addr[0]}:{addr[1]}")
            
            # Determine source and destination
            if side == 'A':  # Packet from caller
                source = session.side_a
                dest = session.side_b
                print(f"[{packet_type}] Direction: Caller -> Callee")
            else:  # Packet from callee
                source = session.side_b
                dest = session.side_a
                print(f"[{packet_type}] Direction: Callee -> Caller")
            
            # NAT learning: update real address from first packet
            if not source.real_ip:
                source.real_ip = addr[0]
                source.real_port = addr[1]
                source.last_packet_time = time.time()
                print(f"[NAT] Learned real address: {source.real_ip}:{source.real_port}")
            
            # Update last packet time
            source.last_packet_time = time.time()
            
            # Forward packet to destination
            if dest.real_ip and dest.real_port:
                # Use learned address (NAT-aware)
                dest_addr = (dest.real_ip, dest.real_port)
                print(f"[{packet_type}] Forwarding to learned address: {dest_addr[0]}:{dest_addr[1]}")
            elif dest.remote_ip and dest.remote_port:
                # Use address from SDP (if no packets received yet)
                dest_addr = (dest.remote_ip, dest.remote_port)
                print(f"[{packet_type}] Forwarding to SDP address: {dest_addr[0]}:{dest_addr[1]}")
            else:
                print(f"[{packet_type}] No destination address available, dropping packet")
                return
            
            # Forward the packet
            self.transport.sendto(data, dest_addr)
            print(f"[{packet_type}] Forwarded successfully\n")
            
        except Exception as e:
            print(f"[ERROR] RTP forwarding error: {e}")


class ControlProtocol(asyncio.DatagramProtocol):
    """UDP Protocol for control messages"""
    
    def __init__(self, proxy):
        self.proxy = proxy
        self.transport = None
    
    def connection_made(self, transport):
        self.transport = transport
    
    def datagram_received(self, data, addr):
        """Called when control message is received"""
        try:
            message = data.decode('utf-8').strip()
            print(f"[CONTROL] Received from {addr}: {message}")
            
            # Handle message in event loop
            asyncio.create_task(self._handle_message(message, addr))
            
        except Exception as e:
            print(f"[ERROR] Control protocol error: {e}")
    
    async def _handle_message(self, message: str, addr: Tuple[str, int]):
        """Handle control message asynchronously"""
        response = await self.proxy.handle_control_message(message, addr)
        print(f"[CONTROL] Sending response: {response}\n")
        self.transport.sendto(response.encode('utf-8'), addr)


class SimpleRTPProxy:
    """Simplified RTPProxy implementation"""
    
    def __init__(self):
        self.sessions: Dict[str, RTPSession] = {}
        self.port_to_session: Dict[int, Tuple[RTPSession, str]] = {}  # port -> (session, side)
        self.port_allocator = PortAllocator(MIN_RTP_PORT, MAX_RTP_PORT)
        self.rtp_transports: Dict[int, asyncio.DatagramTransport] = {}
        self.public_ip = self._get_public_ip()
        self.loop = None
    
    def _get_public_ip(self) -> str:
        """Get server's public IP (simplified - just get local IP)"""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
    
    def _create_session_key(self, call_id: str, from_tag: str, to_tag: str) -> str:
        """Create unique session key"""
        return f"{call_id}:{from_tag}:{to_tag}"
    
    async def _create_rtp_listener(self, port: int):
        """Create UDP listener for RTP/RTCP"""
        try:
            transport, protocol = await self.loop.create_datagram_endpoint(
                lambda: RTPProtocol(port, self),
                local_addr=(RTP_HOST, port)
            )
            self.rtp_transports[port] = transport
            return transport
        except Exception as e:
            print(f"[ERROR] Failed to create listener on port {port}: {e}")
            return None
    
    async def handle_offer(self, call_id: str, from_tag: str, to_tag: str, 
                          remote_ip: str, remote_port: int) -> str:
        """Handle UPDATE/OFFER command from SIP server"""
        print(f"\n[OFFER] Call-ID: {call_id}, From-tag: {from_tag}, To-tag: '{to_tag}'")
        print(f"[OFFER] Remote wants to receive at: {remote_ip}:{remote_port}")
        
        # Create session key
        session_key = self._create_session_key(call_id, from_tag, to_tag)
        print(f"[OFFER] Session key: {session_key}")
        
        # Check if session exists
        session = self.sessions.get(session_key)
        if not session:
            print("[OFFER] Creating new session")
            session = RTPSession(call_id=call_id, from_tag=from_tag, to_tag=to_tag)
            self.sessions[session_key] = session
            print(f"[OFFER] Stored session with key: {session_key}")
            print(f"[OFFER] Current sessions: {list(self.sessions.keys())}")
        else:
            print("[OFFER] Session already exists, updating")
        
        # Allocate ports for side A (caller)
        rtp_port = self.port_allocator.allocate_pair()
        if not rtp_port:
            return "E8"  # No ports available
        
        # Create listeners
        rtp_transport = await self._create_rtp_listener(rtp_port)
        rtcp_transport = await self._create_rtp_listener(rtp_port + 1)
        
        if not rtp_transport or not rtcp_transport:
            self.port_allocator.free_pair(rtp_port)
            return "E7"
        
        # Update session
        session.side_a.proxy_rtp_port = rtp_port
        session.side_a.proxy_rtcp_port = rtp_port + 1
        session.side_a.remote_ip = remote_ip
        session.side_a.remote_port = remote_port
        
        # Map ports to session
        self.port_to_session[rtp_port] = (session, 'A')
        self.port_to_session[rtp_port + 1] = (session, 'A')
        
        print(f"[OFFER] Allocated proxy ports: {rtp_port}/{rtp_port+1}")
        print(f"[OFFER] Session state: {session.state}")
        
        # Return: just port number (rtpproxy format)
        return f"{rtp_port}"
    
    async def handle_answer(self, call_id: str, from_tag: str, to_tag: str,
                           remote_ip: str, remote_port: int) -> str:
        """Handle LOOKUP/ANSWER command from SIP server"""
        print(f"\n[ANSWER] Call-ID: {call_id}, From-tag: {from_tag}, To-tag: {to_tag}")
        print(f"[ANSWER] Remote wants to receive at: {remote_ip}:{remote_port}")
        print(f"[ANSWER] Current sessions: {list(self.sessions.keys())}")
        
        # Find session - the original session was created with to_tag as empty
        # Try looking up with empty to_tag first
        session_key = self._create_session_key(call_id, to_tag, "")
        print(f"[ANSWER] Trying session key: {session_key}")
        session = self.sessions.get(session_key)
        
        if not session:
            # Try with swapped tags (old behavior)
            session_key = self._create_session_key(call_id, to_tag, from_tag)
            print(f"[ANSWER] Not found, trying alternate key: {session_key}")
            session = self.sessions.get(session_key)
        
        if not session:
            # Try with original tags
            session_key = self._create_session_key(call_id, from_tag, to_tag)
            print(f"[ANSWER] Not found, trying another key: {session_key}")
            session = self.sessions.get(session_key)
        
        if not session:
            print("[ANSWER] Session not found! Available sessions:")
            for key in self.sessions.keys():
                print(f"  - {key}")
            return "E1"  # Session not found
        
        print(f"[ANSWER] Session found with key: {session_key}")
        
        # Update to_tag if it was empty
        if not session.to_tag:
            session.to_tag = from_tag
            # Update session key in dictionary
            old_key = self._create_session_key(call_id, to_tag, "")
            new_key = self._create_session_key(call_id, to_tag, from_tag)
            if old_key in self.sessions and old_key != new_key:
                self.sessions[new_key] = session
                del self.sessions[old_key]
                print(f"[ANSWER] Updated session key from {old_key} to {new_key}")
        
        # Allocate ports for side B (callee)
        rtp_port = self.port_allocator.allocate_pair()
        if not rtp_port:
            return "E8"
        
        # Create listeners
        rtp_transport = await self._create_rtp_listener(rtp_port)
        rtcp_transport = await self._create_rtp_listener(rtp_port + 1)
        
        if not rtp_transport or not rtcp_transport:
            self.port_allocator.free_pair(rtp_port)
            return "E7"
        
        # Update session
        session.side_b.proxy_rtp_port = rtp_port
        session.side_b.proxy_rtcp_port = rtp_port + 1
        session.side_b.remote_ip = remote_ip
        session.side_b.remote_port = remote_port
        session.state = "ESTABLISHED"
        
        # Map ports to session
        self.port_to_session[rtp_port] = (session, 'B')
        self.port_to_session[rtp_port + 1] = (session, 'B')
        
        print(f"[ANSWER] Allocated proxy ports: {rtp_port}/{rtp_port+1}")
        print(f"[ANSWER] Session established!")
        print(f"[ANSWER] Side A (caller): {session.side_a.proxy_rtp_port} -> {session.side_a.remote_ip}:{session.side_a.remote_port}")
        print(f"[ANSWER] Side B (callee): {session.side_b.proxy_rtp_port} -> {session.side_b.remote_ip}:{session.side_b.remote_port}")
        
        # Return: just port number
        return f"{rtp_port}"
    
    async def handle_delete(self, call_id: str, from_tag: str, to_tag: str) -> str:
        """Handle DELETE command (BYE received)"""
        print(f"\n[DELETE] Call-ID: {call_id}, From-tag: {from_tag}, To-tag: {to_tag}")
        
        session_key = self._create_session_key(call_id, from_tag, to_tag)
        session = self.sessions.get(session_key)
        
        if not session:
            # Try reversed tags
            session_key = self._create_session_key(call_id, to_tag, from_tag)
            session = self.sessions.get(session_key)
        
        if not session:
            print("[DELETE] Session not found")
            return "E1"
        
        # Close sockets and free ports
        self._cleanup_session(session)
        
        # Remove from sessions
        del self.sessions[session_key]
        
        print(f"[DELETE] Session deleted successfully")
        return "0"
    
    def _cleanup_session(self, session: RTPSession):
        """Clean up session resources"""
        ports = [
            session.side_a.proxy_rtp_port,
            session.side_a.proxy_rtcp_port,
            session.side_b.proxy_rtp_port,
            session.side_b.proxy_rtcp_port
        ]
        
        for port in ports:
            if port and port in self.rtp_transports:
                self.rtp_transports[port].close()
                del self.rtp_transports[port]
            if port:
                self.port_to_session.pop(port, None)
                self.port_allocator.free_pair(port)
    
    async def handle_control_message(self, data: str, addr: Tuple[str, int]) -> str:
        """Parse and handle control protocol messages"""
        parts = data.strip().split()
        if not parts:
            return "E0"
        
        # Real rtpproxy protocol format
        cookie = parts[0] if len(parts) > 0 else ""
        command_and_flags = parts[1] if len(parts) > 1 else ""
        
        if not command_and_flags:
            return f"{cookie} E0"
        
        command = command_and_flags[0]  # First character is the command
        flags = command_and_flags[1:] if len(command_and_flags) > 1 else ""
        
        try:
            if command == 'V':  # VERSION
                # Check if it's VF (version with feature check)
                if len(command_and_flags) > 1 and command_and_flags[1] == 'F':
                    # VF feature_date - check if feature is supported
                    feature_date = parts[2] if len(parts) > 2 else ""
                    # Return 1 for supported, 0 for not supported
                    # For simplicity, support most features
                    unsupported = ['20081224', '20170313']  # Some random unsupported features
                    support = '0' if feature_date in unsupported else '1'
                    return f"{cookie} {support}"
                else:
                    # Just V - return version
                    return f"{cookie} 20040107"
            
            elif command == 'U':  # UPDATE/OFFER
                # Format: cookie Uflags call-id IP port from-tag;to-tag [extra-tag;extra-to-tag]
                call_id = parts[2] if len(parts) > 2 else ""
                remote_ip = parts[3] if len(parts) > 3 else ""
                remote_port = int(parts[4]) if len(parts) > 4 else 0
                tags = parts[5] if len(parts) > 5 else ""

                print(f"[U CMD] Raw tag string: '{tags}'")

                # Parse tags: from-tag;to-tag
                # CRITICAL: ignore any semicolon suffix (e.g. ";1")
                if ';' in tags:
                    from_tag = tags.split(';', 1)[0]
                else:
                    from_tag = tags
                print(f"[U CMD] Extracted from_tag: '{from_tag}'")

                # CRITICAL: Always pass empty string as to_tag for U command
                to_tag = ""
                print(f"[U CMD] Using to_tag: '{to_tag}' (empty)")

                result = await self.handle_offer(call_id, from_tag, to_tag, remote_ip, remote_port)
                return f"{cookie} {result}"


            elif command == 'L':  # LOOKUP/ANSWER
                # Format: cookie Lflags call-id IP port from-tag;to-tag [from-tag2;to-tag2]
                # Example: Lc110,8,0,101 call-id IP port b9147c0f;1 a9vXYgy;1
                # First pair matches the U command's from_tag!
                call_id = parts[2] if len(parts) > 2 else ""
                remote_ip = parts[3] if len(parts) > 3 else ""
                remote_port = int(parts[4]) if len(parts) > 4 else 0
                
                # First tag pair - this matches the ORIGINAL from_tag from U command
                tags1 = parts[5] if len(parts) > 5 else ""
                print(f"[L CMD] First tag pair: {tags1}")
                if ';' in tags1:
                    original_from_tag, _ = tags1.split(';', 1)
                else:
                    original_from_tag = tags1
                
                # Second tag pair - this is the NEW from_tag (callee's tag)
                tags2 = parts[6] if len(parts) > 6 else ""
                print(f"[L CMD] Second tag pair: {tags2}")
                if ';' in tags2:
                    new_from_tag, _ = tags2.split(';', 1)
                else:
                    new_from_tag = tags2
                
                print(f"[L CMD] Extracted - original_from_tag: {original_from_tag}, new_from_tag: {new_from_tag}")
                
                # Use original_from_tag as to_tag and new_from_tag as from_tag for lookup
                result = await self.handle_answer(call_id, new_from_tag, original_from_tag, remote_ip, remote_port)
                return f"{cookie} {result}"
            
            elif command == 'D':  # DELETE
                # Format: cookie D call-id from-tag to-tag (space separated, not semicolon)
                call_id = parts[2] if len(parts) > 2 else ""
                from_tag = parts[3] if len(parts) > 3 else ""
                to_tag = parts[4] if len(parts) > 4 else ""
                
                result = await self.handle_delete(call_id, from_tag, to_tag)
                return f"{cookie} {result}"
            
            elif command == 'I' or command == 'i':  # INFO
                return f"{cookie} sessions: {len(self.sessions)}"
            
            else:
                return f"{cookie} E0"  # Unknown command
        
        except Exception as e:
            print(f"[ERROR] Exception handling command: {e}")
            import traceback
            traceback.print_exc()
            return f"{cookie} E0"
    
    async def run(self):
        """Run the RTPProxy server"""
        self.loop = asyncio.get_event_loop()
        
        # Create control server
        transport, protocol = await self.loop.create_datagram_endpoint(
            lambda: ControlProtocol(self),
            local_addr=(CONTROL_HOST, CONTROL_PORT)
        )
        
        print(f"[CONTROL] Listening on {CONTROL_HOST}:{CONTROL_PORT}")
        print(f"[CONTROL] Public IP: {self.public_ip}")
        print(f"[CONTROL] RTP port range: {MIN_RTP_PORT}-{MAX_RTP_PORT}\n")
        
        # Keep running
        try:
            await asyncio.Event().wait()  # Run forever
        except asyncio.CancelledError:
            pass


async def main():
    """Main entry point"""
    print("=" * 60)
    print("Simple RTPProxy Service")
    print("=" * 60)
    
    proxy = SimpleRTPProxy()
    await proxy.run()


if __name__ == "__main__":
    try:
        loop = asyncio.get_event_loop()
        loop.run_until_complete(main())
    except KeyboardInterrupt:
        print("\n\n[SHUTDOWN] RTPProxy stopped")
    finally:
        loop.close()
