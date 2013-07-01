# This file is part of the MapProxy project.
# Copyright (C) 2010 Omniscale <http://omniscale.de>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Utility methods and classes (file locking, asynchronous execution pools, etc.).
"""
from __future__ import with_statement
import time
import os
import sys
import random
import errno
import shutil
import datetime
import contextlib
from functools import wraps


@contextlib.contextmanager
def local_base_config(conf):
    """
    Temporarily set the global configuration (mapproxy.config.base_config).

    The global mapproxy.config.base_config object is thread-local and
    is set per-request in the MapProxyApp. Use `local_base_config` to
    set base_config outside of a request context (e.g. system loading
    or seeding).
    """
    import mapproxy.config.config
    mapproxy.config.config._config.push(conf)
    try:
        yield
    finally:
        mapproxy.config.config._config.pop()

def reraise_exception(new_exc, exc_info):
    """
    Reraise exception (`new_exc`) with the given `exc_info`.
    """
    _exc_class, _exc, tb = exc_info
    raise new_exc.__class__, new_exc, tb

class cached_property(object):
    """A decorator that converts a function into a lazy property. The
    function wrapped is called the first time to retrieve the result
    and than that calculated result is used the next time you access
    the value::

        class Foo(object):

            @cached_property
            def foo(self):
                # calculate something important here
                return 42
    """

    def __init__(self, func, name=None, doc=None):
        self.func = func
        self.__name__ = name or func.__name__
        self.__doc__ = doc or func.__doc__

    def __get__(self, obj, type=None):
        if obj is None:
            return self
        value = self.func(obj)
        setattr(obj, self.__name__, value)
        return value

def memoize(func):
    @wraps(func)
    def wrapper(self, *args):
        if not hasattr(self, '__memoize_cache'):
            self.__memoize_cache = {}
        cache = self.__memoize_cache.setdefault(func, {})
        if args not in cache:
            cache[args] = func(self, *args)
        return cache[args]
    return wrapper

def swap_dir(src_dir, dst_dir, keep_old=False, backup_ext='.tmp'):
    """
    Rename `src_dir` to `dst_dir`. The `dst_dir` is first renamed to
    `dst_dir` + `backup_ext` to keep the interruption short.
    Then the `src_dir` is renamed. If `keep_old` is False, the old content
    of `dst_dir` will be removed.
    """
    tmp_dir = dst_dir + backup_ext
    if os.path.exists(dst_dir):
        os.rename(dst_dir, tmp_dir)

    _force_rename_dir(src_dir, dst_dir)

    if os.path.exists(tmp_dir) and not keep_old:
        shutil.rmtree(tmp_dir)

def _force_rename_dir(src_dir, dst_dir):
    """
    Rename `src_dir` to `dst_dir`. If `dst_dir` exists, it will be removed.
    """
    # someone might recreate the directory between rmtree and rename,
    # so we try to remove it until we can rename our new directory
    rename_tries = 0
    while rename_tries < 10:
        try:
            os.rename(src_dir, dst_dir)
        except OSError, ex:
            if ex.errno == errno.ENOTEMPTY or ex.errno == errno.EEXIST:
                if rename_tries > 0:
                    time.sleep(2**rename_tries / 100.0) # from 10ms to 5s
                rename_tries += 1
                shutil.rmtree(dst_dir)
            else:
                raise
        else:
            break # on success

def timestamp_before(weeks=0, days=0, hours=0, minutes=0, seconds=0):
    """
    >>> time.time() - timestamp_before(minutes=1) - 60 <= 1
    True
    >>> time.time() - timestamp_before(days=1, minutes=2) - 86520 <= 1
    True
    >>> time.time() - timestamp_before(hours=2) - 7200 <= 1
    True
    """
    delta = datetime.timedelta(weeks=weeks, days=days, hours=hours, minutes=minutes, seconds=seconds)
    before = datetime.datetime.now() - delta
    return time.mktime(before.timetuple())

def timestamp_from_isodate(isodate):
    """
    >>> ts = timestamp_from_isodate('2009-06-09T10:57:00')
    >>> # we don't know which timezone the test will run
    >>> (1244537820.0 - 14 * 3600) < ts < (1244537820.0 + 14 * 3600)
    True
    >>> timestamp_from_isodate('2009-06-09T10:57') #doctest: +ELLIPSIS
    Traceback (most recent call last):
    ...
    ValueError: ...
    """
    if isinstance(isodate, datetime.datetime):
        date = isodate
    else:
        date = datetime.datetime.strptime(isodate, "%Y-%m-%dT%H:%M:%S")
    return time.mktime(date.timetuple())

def cleanup_directory(directory, before_timestamp, remove_empty_dirs=True,
                      file_handler=None):
    if file_handler is None:
        if before_timestamp == 0 and remove_empty_dirs == True and os.path.exists(directory):
            shutil.rmtree(directory, ignore_errors=True)
            return

        file_handler = os.remove

    if os.path.exists(directory):
        for dirpath, dirnames, filenames in os.walk(directory, topdown=False):
            if not filenames:
                if (remove_empty_dirs and not os.listdir(dirpath)
                    and dirpath != directory):
                    os.rmdir(dirpath)
                continue
            for filename in filenames:
                filename = os.path.join(dirpath, filename)
                try:
                    if before_timestamp == 0:
                        file_handler(filename)
                    if os.lstat(filename).st_mtime < before_timestamp:
                        file_handler(filename)
                except OSError, ex:
                    if ex.errno != errno.ENOENT: raise

            if remove_empty_dirs:
                remove_dir_if_emtpy(dirpath)

        if remove_empty_dirs:
            remove_dir_if_emtpy(directory)

def remove_dir_if_emtpy(directory):
    try:
        os.rmdir(directory)
    except OSError, ex:
        if ex.errno != errno.ENOENT and ex.errno != errno.ENOTEMPTY: raise

def ensure_directory(file_name):
    """
    Create directory if it does not exist, else do nothing.
    """
    dir_name = os.path.dirname(file_name)
    if not os.path.exists(dir_name):
        try:
            os.makedirs(dir_name)
        except OSError, e:
            if e.errno != errno.EEXIST:
                raise e

def write_atomic(filename, data):
    """
    write_atomic writes `data` to a random file in filename's directory
    first and renames that file to filename. Rename is atomic on all
    Posix platforms.

    Falls back to normal write on Windows.
    """
    if not sys.platform.startswith('win'):
        # write to random filename
        path_tmp = filename + '.tmp-' + str(random.randint(0, 999999))
        try:
            fd = os.open(path_tmp, os.O_EXCL | os.O_CREAT | os.O_WRONLY)
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            os.rename(path_tmp, filename)
        except OSError:
            try:
                os.unlink(path_tmp)
            except OSError:
                pass
            raise
    else:
        with open(filename, 'wb') as f:
            f.write(data)


def replace_instancemethod(old_method, new_method):
    """
    Replace an instance method.

    >>> class Foo(object):
    ...    val = 'bar'
    ...    def bar(self):
    ...        return self.val
    >>> f = Foo()
    >>> f.bar()
    'bar'
    >>> replace_instancemethod(f.bar, lambda self: 'foo' + self.val)
    >>> f.bar()
    'foobar'
    """
    cls = old_method.im_class
    obj = old_method.im_self
    name = old_method.im_func.func_name
    instancemethod = type(old_method)
    setattr(obj, name, instancemethod(new_method, obj, cls))

