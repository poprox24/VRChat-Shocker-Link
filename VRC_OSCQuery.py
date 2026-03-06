from http.server import BaseHTTPRequestHandler, HTTPServer
from pythonosc.osc_server import BlockingOSCUDPServer
from pythonosc.udp_client import SimpleUDPClient
from pythonosc.dispatcher import Dispatcher
from zeroconf import Zeroconf, ServiceInfo
import socket, threading, json
from typing import Callable
import logging

RED = "\033[31m"
YELLOW = "\033[33m"
CYAN = "\033[36m"

logging.basicConfig(
    level=logging.INFO,
    format='%(message)s'
    )

# Used for sending messages to VRChat
def vrc_client(vrchat_host) -> SimpleUDPClient:
    return SimpleUDPClient(vrchat_host, 9000)


# Simple function that turns a dictionary to a dispatcher
def dict_to_dispatcher(routes: dict[str, Callable]) -> Dispatcher:
    d = Dispatcher()
    for route, handler in routes.items():
        d.map(route, handler)
    return d


# Starts the OSC and HTTP discovery server
def start_osc(name: str, dispatcher: Dispatcher, params: set[str] = None) -> Zeroconf:
    try:
        osc_server = BlockingOSCUDPServer(("127.0.0.1", 0), dispatcher)
    except Exception as e:
        logging.error(f"[VRC OSC] {RED}Failed to create OSC server: {e}")
        return None
    
    osc_port = osc_server.server_address[1]
    root_done = False
    host_done = False

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            nonlocal root_done, host_done
            self.send_response(200)
            self.end_headers()
            if "HOST_INFO" in self.path:
                self.wfile.write(json.dumps({"OSC_PORT": osc_port}).encode())
                host_done = True
            else:
                self.wfile.write(json.dumps({"CONTENTS": {
                    "avatar": {
                        "FULL_PATH": "/avatar",
                        "CONTENTS": {
                            "parameters": {
                                "FULL_PATH": "/avatar/parameters",
                                "CONTENTS": {
                                    p: {"FULL_PATH": f"/avatar/parameters/{p}"}
                                    for p in (params or set())
                                }
                            }
                        }
                    }
                }}).encode())
                root_done = True
            if root_done and host_done:
                # Stop the HTTP discovery thread after server is discovered by VRChat
                logging.info(f"[VRC OSC] {CYAN}Server discovered by VRChat.\n[VRC OSC] Shutting down HTTP discovery server.")
                threading.Thread(target=httpd.shutdown, daemon=True).start()
        def log_message(self, *a): pass

    try:
        httpd = HTTPServer(("127.0.0.1", 0), Handler)
    except Exception as e:
        logging.error(f"[VRC OSC] {RED}Failed to create HTTP server: {e}")
        osc_server.server_close()
        return None
    
    http_port = httpd.server_address[1]

    zc = Zeroconf()
    zc.register_service(ServiceInfo(
        "_oscjson._tcp.local.",
        f"{name}._oscjson._tcp.local.",
        addresses=[socket.inet_aton("127.0.0.1")],
        port=http_port
    ))

    threading.Thread(target=osc_server.serve_forever, daemon=True).start()
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    
    return zc