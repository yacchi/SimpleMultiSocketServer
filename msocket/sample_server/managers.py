# -*- coding: utf-8 -*-
from __future__ import absolute_import

import multiprocessing.managers
from multiprocessing.managers import SyncManager as _SyncManager
import threading

__author__ = 'yasu'


class ManagerServer(multiprocessing.managers.Server):
    manager = multiprocessing.managers.SyncManager

    @property
    def socket(self):
        return self.listener._listener._socket

    def dispatch(self, sock):
        '''
        Run the server forever
        '''

        multiprocessing.managers.current_process()._manager_server = self

        c = self.listener.accept()
        t = threading.Thread(target=self.handle_request, args=(c, ))
        t.daemon = True
        t.start()

    def server_close(self):
        pass

    def create(self, c, typeid, *args, **kwds):
        '''
        Create a new shared object and return its id
        '''
        self.mutex.acquire()
        try:
            ident = kwds.pop("ident", None)
            if ident is not None:
                ident = "%s_%s" % (typeid, ident)

            callable, exposed, method_to_typeid, proxytype = \
                self.registry[typeid]

            if ident is None or ident not in self.id_to_obj:
                if callable is None:
                    assert len(args) == 1 and not kwds
                    obj = args[0]
                else:
                    obj = callable(*args, **kwds)

                if not ident:
                    ident = '%x' % id(obj)  # convert to string because xmlrpclib

            else:
                obj = self.id_to_obj[ident][0]

            if exposed is None:
                exposed = multiprocessing.managers.public_methods(obj)
            if method_to_typeid is not None:
                assert type(method_to_typeid) is dict
                exposed = list(exposed) + list(method_to_typeid)

            # only has 32 bit signed integers
            multiprocessing.managers.util.debug('%r callable returned object with id %r', typeid, ident)

            self.id_to_obj[ident] = (obj, set(exposed), method_to_typeid)
            if ident not in self.id_to_refcount:
                self.id_to_refcount[ident] = 0
            # increment the reference count immediately, to avoid
            # this object being garbage collected before a Proxy
            # object for it can be created.  The caller of create()
            # is responsible for doing a decref once the Proxy object
            # has been created.
            self.incref(c, ident)
            return ident, tuple(exposed)
        finally:
            self.mutex.release()


class SyncManager(_SyncManager):
    _Server = ManagerServer

    def get_server(self):
        '''
        Return server object with serve_forever() method and address attribute
        '''
        assert self._state.value == multiprocessing.managers.State.INITIAL
        return self._Server(self._registry, self._address,
                            self._authkey, self._serializer)
