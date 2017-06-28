# -*- coding:utf8 -*-
from __future__ import absolute_import

import os
import socket
import select
import errno
import time
import threading
import logging

from .compat import string_class, socketserver

logger = logging.getLogger("msocket.server")
__author__ = 'fujie'


class ThreadLocalMeta(type):
    def __new__(mcs, name, bases, attrs):
        local = threading.local()
        for k, v in attrs.items():
            if v is not None:
                continue
            attrs[k] = mcs.__make_local_property(local, k)
        attrs["__local__"] = local
        return type.__new__(mcs, name, bases, attrs)

    @classmethod
    def __make_local_property(mcs, local, name):
        l = local
        setattr(l, name, None)
        return property(
            (lambda _: getattr(l, name)),   # getter
            (lambda _, val: setattr(l, name, val)),  # setter
            (lambda _: delattr(l, name))    # deleter
        )


class LocalRequest(object):
    __metaclass__ = ThreadLocalMeta

    reactor = None
    socket = None
    server = None
    handler = None
    close_connection = None


request_context = LocalRequest()


class SocketWrapper(object):
    def __init__(self, server_address):
        if isinstance(server_address, list):
            server_address = tuple(server_address)

        self.server_address = server_address
        self._bind = False
        self._activate = False
        self.socket = None
        self.request_handler = None

    def set_request_handler(self, handler):
        self.request_handler = handler

    def close(self):
        if self._bind and self._activate:
            self.socket.close()
            self._bind = False
            self._activate = False

    def closed(self):
        return not (self._bind and self._activate)

    def __getattr__(self, item):
        return getattr(self.socket, item)

    def __unicode__(self):
        address = self.server_address
        if isinstance(address, tuple):
            address = "%s:%d" % address
        elif address[0] == "\0":
            address = address.replace("\0", "@")
        return address

    def __str__(self):
        return self.__unicode__().encode()

    def __repr__(self):
        address = self.server_address
        if address[0] == "\0":
            address = address.replace("\0", "@")

        return "<%s(%s) at %d>" % (self.__class__.__name__, address, self.fileno())


class StreamSocket(SocketWrapper):
    socket_type = socket.SOCK_STREAM

    def __init__(self, server_address, address_family=socket.AF_INET, request_queue_size=5, allow_reuse_address=False):
        SocketWrapper.__init__(self, server_address)

        self.allow_reuse_address = allow_reuse_address
        self.socket = socket.socket(address_family, self.socket_type)
        self.request_queue_size = request_queue_size

    def server_bind(self):
        if self._bind:
            return
        _socket = self.socket
        if self.allow_reuse_address:
            _socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        _socket.bind(self.server_address)
        self.server_address = _socket.getsockname()
        self._bind = True

    def server_activate(self):
        if self._activate:
            return
        self.socket.listen(self.request_queue_size)
        self._activate = True

    def accept(self):
        request, client_address = self.socket.accept()
        if not client_address:
            client_address = (self.server_address, 0)
        return request, client_address


class AcceptedStreamSocket(SocketWrapper):
    def __init__(self, request, client_address):
        server_address = request.getsockname()
        SocketWrapper.__init__(self, server_address)
        self.client_address = client_address
        self._bind = True
        self._activate = True
        self.socket = request

    def accept(self):
        return self, self.client_address

    def __unicode__(self):
        address = self.client_address
        if isinstance(address, tuple):
            address = "%s:%d" % address
        elif isinstance(address, string_class) and len(address) and address[0] == "\0":
            address = address.replace("\0", "@")
        return address

    def __repr__(self):
        address = self.client_address
        if isinstance(address, string_class) and len(address) and address[0] == "\0":
            address = address.replace("\0", "@")

        return "<%s(%s) at %d>" % (self.__class__.__name__, address, self.fileno())


def make_poller():
    if select.select.__module__ != 'select':
        return SelectPoller()
    if hasattr(select, 'epoll'):
        return EPollPoller()
    if hasattr(select, 'poll'):
        return PollPoller()
    return SelectPoller()


class SelectPoller(object):
    def __init__(self):
        self._fds = []

    def release(self):
        self._fds = []

    def register(self, fd):
        if not isinstance(fd, int):
            fd = fd.fileno()
        if fd not in self._fds:
            self._fds.append(fd)

    def unregister(self, fd):
        if not isinstance(fd, int):
            fd = fd.fileno()
        if fd in self._fds:
            self._fds.remove(fd)

    def poll(self, poll_interval):
        if not self._fds:
            time.sleep(poll_interval)
            return []

        try:
            r, w, x = select.select(self._fds, [], [], poll_interval)
            return r
        except (OSError, IOError, select.error) as e:
            if e.args[0] == errno.EINTR:
                return []
            raise


class PollPoller(object):
    try:
        mask = select.POLLIN | select.POLLPRI
        poller = select.poll
    except AttributeError:
        pass

    interval_scale = 1000

    def __init__(self):
        self._poller = self.poller()

    def release(self):
        pass

    def register(self, fd):
        try:
            self._poller.register(fd, self.mask)
        except IOError:
            pass

    def unregister(self, fd):
        if not isinstance(fd, int):
            fd = fd.fileno()
        if fd < 1:
            return

        try:
            self._poller.unregister(fd)
        except IOError:
            pass

    def poll(self, poll_interval):
        try:
            events = self._poller.poll(poll_interval * self.interval_scale)
            for fd, event in events:
                if event | self.mask:
                    yield fd
        except (OSError, IOError, select.error) as e:
            if e.args[0] != errno.EINTR:
                raise


class EPollPoller(PollPoller):
    try:
        mask = select.EPOLLIN | select.EPOLLPRI
        poller = select.epoll
    except AttributeError:
        pass
    interval_scale = 1

    def release(self):
        self._poller.close()


class Reactor(object):
    def __init__(self):
        self._servers = {}
        self.lock = threading.Lock()
        self.__shutdown_request = False
        self.__is_shut_down = threading.Event()
        self.__poller = make_poller()

    def sockets(self):
        return sorted([sock for _, sock in self._servers.values()], key=lambda s: s.fileno())

    def get_servers(self):
        servers = []
        for server, _ in self._servers.values():
            if server not in servers:
                servers.append(server)
        return servers

    def add_listener(self, server, sock):
        fd = sock.fileno()
        if fd not in self._servers:
            with self.lock:
                self._servers[fd] = (server, sock)
                self.__poller.register(sock)
                if isinstance(sock, AcceptedStreamSocket):
                    logger.info("Managing socket %s", sock)
                elif isinstance(sock, SocketWrapper):
                    logger.info("Listen on %s for %s", sock, server.__class__.__name__)
                else:
                    address = sock.getsockname() or sock
                    if isinstance(address, (tuple, list)):
                        address = "%s:%s" % tuple(address[:2])
                    logger.info("Listen on %s for %s", address, server.__class__.__name__)

    def add_server(self, server, sock=None):
        if sock is None:
            if hasattr(server, 'socket'):
                sock = server.socket

        return self.add_listener(server, sock)

    def del_listener(self, sock):
        fd = sock.fileno()
        if fd in self._servers:
            with self.lock:
                self._servers.pop(fd, None)
                try:
                    self.__poller.unregister(sock)
                except IOError:
                    pass
                if isinstance(sock, AcceptedStreamSocket):
                    logger.info("Removing socket %s", sock)
                elif isinstance(sock, SocketWrapper):
                    logger.info("Shutdown serving socket %s", sock)
                else:
                    address = sock.getsockname() or sock
                    if isinstance(address, (tuple, list)):
                        address = "%s:%s" % tuple(address[:2])
                    logger.info("Shutdown serving socket %s", address)

    def del_server(self, server):
        if hasattr(server, 'socket'):
            sock = server.socket
            return self.del_listener(sock)

    def run(self, poll_interval=0.5):
        self.__is_shut_down.clear()
        request_context.reactor = self
        try:
            poller = self.__poller
            while not self.__shutdown_request:
                r = poller.poll(poll_interval)

                for fd in r:
                    server = self._servers.get(fd)
                    if server is None:
                        poller.unregister(fd)
                        continue
                    request_context.server = server[0]
                    request_context.socket = server[1]
                    request_context.close_connection = True
                    server[0].dispatch(server[1])

        finally:
            self.__shutdown_request = False
            self.__is_shut_down.set()

    def server_close(self):
        self.__poller.release()

    def shutdown(self):
        if not self.__shutdown_request:
            self.__shutdown_request = True
            # self.__is_shut_down.wait()
            self.server_close()


class ExternalReactorMixIn:
    def get_reactor(self):
        """
        :rtype: Reactor
        """
        return self.__reactor__

    def dispatch(self, sock):
        setattr(self, '_socket', sock)
        return self._handle_request_noblock()

    def get_request(self):
        if hasattr(self, '_socket'):
            request, client_address = self._socket.accept()
        else:
            request, client_address = self.socket.accept()

        if not isinstance(request, SocketWrapper):
            request = AcceptedStreamSocket(request, client_address)
        if client_address:
            if isinstance(client_address[0], string_class) and client_address[0][0] == "\0":
                client_address = (client_address[0].replace("\0", "@"), client_address[1])
        else:
            sock_name = request.server_address
            if isinstance(sock_name, tuple):
                client_address = sock_name
            else:
                client_address = (sock_name, 0)
        return request, client_address

    def finish_request(self, request, client_address):
        request_context.reactor = self.get_reactor()
        request_context.server = self
        request_context.socket = request
        request_context.close_connection = True
        try:
            self.RequestHandlerClass(request, client_address, self)
        except socket.error, e:
            if e.errno != errno.EPIPE:
                raise


class MultiSocketServer(object):
    def __init__(self, reactor=None, log_stdout=True):
        self.servers = []
        if reactor:
            self.reactor = reactor
        else:
            self.reactor = Reactor()

        logger.setLevel(logging.INFO)

        if log_stdout:
            h = logging.StreamHandler()
            h.setLevel(logging.INFO)
            logfmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
            h.setFormatter(logfmt)
            logger.addHandler(h)
        else:
            logger.addHandler(logging.NullHandler())

    def add_server(self, server):
        self.reactor.add_server(server)
        if server not in self.servers:
            setattr(server, '__reactor__', self.reactor)
            self.servers.append(server)

    def run(self, poll_interval=0.5):
        logger.info("Start serving")
        self.reactor.run(poll_interval)

    def shutdown(self):
        logger.info("Server stopping")
        for server in reversed(self.servers):
            if hasattr(server, 'server_close'):
                server.server_close()
                self.reactor.del_server(server)
        self.reactor.shutdown()


class TCPServer(ExternalReactorMixIn, socketserver.TCPServer):
    def __init__(self, server_address, RequestHandlerClass, bind_and_activate=True):
        socketserver.TCPServer.__init__(self, server_address, RequestHandlerClass, bind_and_activate=False)
        self.socket.close()

        if self.address_family == socket.AF_INET or self.address_family == socket.AF_INET6:
            info = socket.getaddrinfo(server_address[0], None)[0]
            self.address_family = info[0]

        self.socket = StreamSocket(server_address, self.address_family, self.request_queue_size,
                                   self.allow_reuse_address)
        if bind_and_activate:
            self.server_bind()
            self.server_activate()

    def server_bind(self):
        if isinstance(self.server_address, string_class):
            if self.allow_reuse_address and not self.server_address.startswith("\0"):
                if os.path.exists(self.server_address):
                    os.unlink(self.server_address)

        socketserver.TCPServer.server_bind(self)


class ForkingTCPServer(socketserver.ForkingMixIn, TCPServer):
    pass


class ThreadingTCPServer(socketserver.ThreadingMixIn, TCPServer):
    pass


if hasattr(socket, 'AF_UNIX'):
    class UnixStreamServer(TCPServer):
        address_family = socket.AF_UNIX
        allow_reuse_address = True


    class ThreadingUnixStreamServer(socketserver.ThreadingMixIn, UnixStreamServer):
        pass


    class ForkingUnixStreamServer(socketserver.ForkingMixIn, UnixStreamServer):
        pass
