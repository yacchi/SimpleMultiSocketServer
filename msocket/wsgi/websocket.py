# -*- coding:utf8 -*-
from __future__ import absolute_import

import threading
from ..server import AcceptedStreamSocket, request_context
from ..compat import py3k
from .handlers import SimpleHandler as _SimpleHandler, WSGIRequestHandler as _WSGIRequestHandler
import wsgiref.util

wsgiref.util._hoppish = {}.__contains__


class AcceptedWebSocket(AcceptedStreamSocket):
    def __init__(self, environ):
        sock_file = environ['wsgi.input']
        if py3k:
            request = sock_file.raw._sock
        else:
            request = sock_file._sock
        client_address = (environ.get('REMOTE_ADDR'), environ.get('REMOTE_PORT'))
        super(AcceptedWebSocket, self).__init__(request, client_address)
        self.websocket = True
        self.ws = None
        self.lock = threading.Lock()


class WebSocketManager(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.closed = False
        self._socks = {}

    def add_ws(self, sock):
        fd = sock.fileno()
        self._socks[fd] = sock
        request_context.reactor.add_listener(self, sock)

    def del_ws(self, sock):
        request_context.reactor.del_listener(sock)
        self._socks.pop(sock.fileno(), None)

    def dispatch(self, request):
        """
        :param request: socket wrapper object for websocket connection
        :type request: AcceptedWebSocket
        :param client_address
        """
        ws = request.ws
        with request.lock:
            if ws and not ws.terminated:
                try:
                    if not ws.once():
                        self.del_ws(request)

                        if not ws.terminated:
                            ws.terminate()
                except:
                    import traceback
                    traceback.print_exc()
                    pass

    def websockets(self):
        return (s for s in self._socks.values())

    def close_all(self, code=1001, message='Server is shutting down'):
        with self.lock:
            for ws in (s.ws for s in self.websockets()):
                ws.close(code=code, reason=message)

    def broadcast(self, message, binary=False):
        with self.lock:
            websockets = self.websockets()

        for ws in (s.ws for s in websockets):
            if not ws.terminated:
                try:
                    ws.send(message, binary)
                except:
                    pass

    def server_close(self):
        if self.closed:
            return
        self.close_all()


class SimpleHandler(_SimpleHandler):
    websocket_manager = None

    def setup_environ(self):
        """
        Setup the environ dictionary and add the
        `'ws4py.socket'` key. Its associated value
        is the real socket underlying socket.
        """
        _SimpleHandler.setup_environ(self)

        if self.websocket_manager is None:
            raise Exception("WebSocket Manager is None")

        if self.environ.get('HTTP_UPGRADE', '').lower() == 'websocket':
            self.environ['ws4py.socket'] = AcceptedWebSocket(self.environ)

    def finish_response(self):
        """
        Completes the response and performs the following tasks:

        - Remove the `'ws4py.socket'` and `'ws4py.websocket'`
          environ keys.
        - Attach the returned websocket, if any, to the WSGI server
          using its ``link_websocket_to_server`` method.
        """
        ws = None
        if self.environ:
            sock = self.environ.pop('ws4py.socket', None)
            if sock:
                ws = self.environ.get('ws4py.websocket')
                sock.ws = ws

        try:
            _SimpleHandler.finish_response(self)

        except:
            if ws:
                ws.close(1011, reason='Something broke')
            raise
        else:
            if ws:
                self.websocket_manager.add_ws(sock)


def get_request_handler(websocket_manager):
    manager = websocket_manager

    class SimpleHandlerWS(SimpleHandler):
        websocket_manager = manager

    class WSGIRequestHandler(_WSGIRequestHandler):
        wsgi_handler = SimpleHandlerWS

    return WSGIRequestHandler
