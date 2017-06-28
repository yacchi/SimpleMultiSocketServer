# -*- coding:utf8 -*-
from __future__ import absolute_import

import os
import socket
import threading
from wsgiref.simple_server import WSGIServer as _WSGIServer

from ..compat import string_class, socketserver, address_type
from ..server import (ExternalReactorMixIn, SocketWrapper, StreamSocket, AcceptedStreamSocket, MultiSocketServer,
                      request_context)

from .handlers import WSGIRequestHandler


# noinspection PyClassHasNoInit
class StreamSocketWrappingMixIn:
    def get_request(self):
        # noinspection PyUnresolvedReferences
        request, client_address = self.socket.accept()
        if not isinstance(request, SocketWrapper):
            request = AcceptedStreamSocket(request, client_address)
        if isinstance(client_address[0], string_class) and client_address[0][0] == "\0":
            client_address = (client_address[0].replace("\0", "@"), client_address[1])
        return request, client_address


# noinspection PyClassHasNoInit
class SocketWrapperWSGIServer(StreamSocketWrappingMixIn, _WSGIServer):
    lock = threading.Lock()

    def handle_error(self, request, client_address):
        with self.lock:
            _WSGIServer.handle_error(self, request, client_address)

    def shutdown_request(self, request):
        if request_context.close_connection:
            _WSGIServer.shutdown_request(self, request)


class INETSocketWSGIServer(SocketWrapperWSGIServer):
    # noinspection PyPep8Naming
    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        SocketWrapperWSGIServer.__init__(self, server_address, RequestHandlerClass, bind_and_activate=False)
        self.socket.close()
        if self.address_family == socket.AF_INET or self.address_family == socket.AF_INET6:
            info = socket.getaddrinfo(server_address[0], None)[0]
            self.address_family = info[0]
        self.socket = StreamSocket(server_address, self.address_family, self.request_queue_size,
                                   self.allow_reuse_address)

        if bind_and_activate:
            self.server_bind()
            self.server_activate()


class UnixSocketWSGIServer(INETSocketWSGIServer):
    address_family = socket.AF_UNIX
    server_name = None
    server_port = None

    def server_bind(self):
        """Override server_bind to store the server name."""
        server_address = self.socket.server_address
        if not server_address.startswith("\0") and os.path.exists(server_address):
            os.unlink(server_address)

        socketserver.TCPServer.server_bind(self)
        self.server_name = server_address.replace("\0", "@")
        self.server_port = 0
        self.setup_environ()


class MultiSocketWSGIServer(MultiSocketServer):
    class INETServer(ExternalReactorMixIn, INETSocketWSGIServer):
        pass

    class UnixSocketServer(ExternalReactorMixIn, UnixSocketWSGIServer):
        pass

    wsgi_servers = {
        'AF_INET': INETServer,
        'AF_INET6': INETServer,
        'AF_UNIX': UnixSocketServer,
        socket.AF_INET: INETServer,
        socket.AF_INET6: INETServer,
        socket.AF_UNIX: UnixSocketServer,
    }

    def __init__(self, app=None, handler_cls=WSGIRequestHandler, reactor=None, log_stdout=True):
        super(MultiSocketWSGIServer, self).__init__(reactor, log_stdout)
        self.handler_cls = handler_cls
        self.application = app

    def add_server(self, server, app=None):
        if isinstance(server, _WSGIServer):
            if app is None:
                app = self.application
            server.set_app(app)
        super(MultiSocketWSGIServer, self).add_server(server)

    def wsgi_server(self, server_address, address_family=None, app=None, handler_cls=None,
                    thread=True):
        if app is None:
            app = self.application
        if handler_cls is None:
            handler_cls = self.handler_cls

        if address_family is None:
            address_family = address_type(server_address)

        server_cls = self.wsgi_servers.get(address_family)

        if not server_cls:
            return

        if thread and not issubclass(server_cls, socketserver.ThreadingMixIn):
            class Server(socketserver.ThreadingMixIn, server_cls):
                daemon_threads = True

            Server.__name__ = server_cls.__name__
            server_cls = Server

        server = server_cls(server_address, handler_cls)
        self.add_server(server, app)
        return server


def load(target, **namespace):
    import sys
    """ Import a module or fetch an object from a module.

        * ``package.module`` returns `module` as a module object.
        * ``pack.mod:name`` returns the module variable `name` from `pack.mod`.
        * ``pack.mod:func()`` calls `pack.mod.func()` and returns the result.

        The last form accepts not only function calls, but any type of
        expression. Keyword arguments passed to this function are available as
        local variables. Example: ``import_string('re:compile(x)', x='[a-z]')``
    """
    mod, target = target.split(":", 1) if ':' in target else (target, None)
    if mod not in sys.modules: __import__(mod)
    if not target: return sys.modules[mod]
    if isinstance(target, (str, string_class)) and target.isalnum(): return getattr(sys.modules[mod], target)
    package_name = mod.split('.')[0]
    namespace[package_name] = sys.modules[package_name]
    return eval('%s.%s' % (mod, target), namespace)


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--address", dest="bind", help="bind socket to address")
    parser.add_argument("application", metavar="package.module:app")
    args = parser.parse_args()

    host, port = (args.bind or 'localhost'), 8080
    if ':' in host and host.rfind(']') < host.rfind(':'):
        host, port = host.rsplit(':', 1)
    host = host.strip('[]')

    app = load(args.application)
    server = MultiSocketWSGIServer()
    server.wsgi_server((host, int(port)), app=app)

    try:
        server.run()
    except KeyboardInterrupt:
        server.shutdown()

if __name__ == '__main__':
    main()
