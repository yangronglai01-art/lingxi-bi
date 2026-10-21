from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import socket
import threading
import time
import pytest
from scripts.local_load_transport import LocalLoadClient

@contextmanager
def server():
    counts={'get':0,'post':0}
    class Handler(BaseHTTPRequestHandler):
        protocol_version='HTTP/1.1'
        def setup(self):
            super().setup();self.connection.settimeout(.15)
        def log_message(self,*args):pass
        def do_GET(self):
            counts['get']+=1
            payload=json.dumps({'ok':True}).encode()
            self.send_response(429 if self.path=='/limited' else 200)
            self.send_header('Content-Length',str(len(payload)))
            self.send_header('Retry-After','5');self.end_headers();self.wfile.write(payload)
        def do_POST(self):
            counts['post']+=1
            self.rfile.read(int(self.headers.get('Content-Length','0')))
            self.close_connection=True
            self.connection.shutdown(socket.SHUT_RDWR)
    instance=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=instance.serve_forever,daemon=True);thread.start()
    try:yield 'http://127.0.0.1:'+str(instance.server_port),counts
    finally:instance.shutdown();instance.server_close();thread.join(timeout=2)

def test_active_connection_is_reused():
    with server() as (url,counts):
        client=LocalLoadClient(url)
        try:
            assert client.request('/',{})[1]==200
            assert client.request('/',{})[1]==200
            assert len(client.connections)==1 and counts['get']==2
        finally:client.close()

def test_expired_idle_connection_is_replaced_before_request():
    with server() as (url,counts):
        client=LocalLoadClient(url,idle_seconds=.05)
        try:
            assert client.request('/',{})[1]==200
            time.sleep(.25)
            assert client.request('/',{})[1]==200
            assert client.idle_renewals==1 and len(client.connections)==2 and counts['get']==2
        finally:client.close()

def test_failed_post_is_not_replayed():
    with server() as (url,counts):
        client=LocalLoadClient(url)
        try:
            with pytest.raises(RuntimeError,match='HTTP phase='):client.request('/',{},body={'question':'test'})
            assert counts['post']==1
        finally:client.close()

def test_http_rejection_and_retry_header_are_preserved():
    with server() as (url,counts):
        client=LocalLoadClient(url)
        try:
            value,status,_,headers=client.request('/limited',{})
            assert status==429 and headers['retry-after']=='5' and counts['get']==1
        finally:client.close()
