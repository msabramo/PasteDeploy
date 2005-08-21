import os
import re
import urllib
from ConfigParser import RawConfigParser
import pkg_resources

__all__ = ['loadapp', 'loadserver', 'loadfilter']

############################################################
## Utility functions
############################################################

def import_string(s):
    return pkg_resources.EntryPoint.parse("x="+s).load(False)

def _aslist(obj):
    """
    Turn object into a list; lists and tuples are left as-is, None
    becomes [], and everything else turns into a one-element list.
    """
    if obj is None:
        return []
    elif isinstance(obj, (list, tuple)):
        return obj
    else:
        return [obj]

def _flatten(lst):
    """
    Flatten a nested list.
    """
    if not isinstance(lst, (list, tuple)):
        return [lst]
    result = []
    for item in lst:
        result.extend(_flatten(item))
    return result

############################################################
## Object types
############################################################

class _ObjectType(object):

    def __init__(self, name, egg_protocols, config_prefixes):
        self.name = name
        self.egg_protocols = map(_aslist, _aslist(egg_protocols))
        self.config_prefixes = map(_aslist, _aslist(config_prefixes))

    def __repr__(self):
        return '<%s protocols=%r prefixes=%r>' % (
            self.name, self.egg_protocols, self.config_prefixees)

    def invoke(self, context):
        assert context.protocol in _flatten(self.egg_protocols)
        return context.object(context.global_conf, **context.local_conf)

APP = _ObjectType(
    'application',
    ['paste.app_factory1', 'paste.composit_factory1'],    
    [['app', 'application'], 'composit'])

def APP_invoke(context):
    if context.protocol == 'paste.composit_factory1':
        return context.object(context.loader, context.global_conf,
                              **context.local_conf)
    elif context.protocol == 'paste.app_factory1':
        return context.object(context.global_conf, **context.local_conf)
    else:
        assert 0, "Protocol %r unknown" % context.protocol

APP.invoke = APP_invoke

FILTER = _ObjectType(
    'filter',
    ['paste.filter_factory1'],
    ['filter'])

SERVER = _ObjectType(
    'server',
    ['paste.server_factory1'],
    ['server'])

############################################################
## Loaders
############################################################

def loadapp(uri, name=None, **kw):
    return loadobj(APP, uri, name=name, **kw)

def loadfilter(uri, name=None, **kw):
    return loadobj(FILTER, uri, name=name, **kw)

def loadserver(uri, name=None, **kw):
    return loadobj(SERVER, uri, name=name, **kw)

_loaders = {}

def loadobj(object_type, uri, name=None, relative_to=None,
            global_conf=None):
    context = loadcontext(
        object_type, uri, name=name, relative_to=relative_to,
        global_conf=global_conf)
    return context.create()

def loadcontext(object_type, uri, name=None, relative_to=None,
                global_conf=None):
    if '#' in uri:
        if name is None:
            uri, name = uri.split('#', 1)
        else:
            # @@: Ignore fragment or error?
            uri = uri.split('#', 1)[0]
    scheme, path = uri.split(':', 1)
    scheme = scheme.lower()
    if scheme not in _loaders:
        raise LookupError(
            "URI scheme not known: %r (from %s)"
            % (scheme, ', '.join(_loaders.keys())))
    return _loaders[scheme](
        object_type,
        uri, path, name=name, relative_to=relative_to,
        global_conf=global_conf)

def _loadconfig(object_type, uri, path, name, relative_to,
                global_conf):
    # De-Windowsify the paths:
    path = path.replace('\\', '/')
    if not path.startswith('/'):
        if not relative_to:
            raise ValueError(
                "Cannot resolve relative uri %r; no context keyword "
                "argument given" % uri)
        relative_to = relative_to.replace('\\', '/')
        if relative_to.endswith('/'):
            path = relative_to + path
        else:
            path = relative_to + '/' + path
    if path.startswith('///'):
        path = path[2:]
    path = urllib.unquote(path)
    loader = ConfigLoader(path)
    return loader.get_context(object_type, name, global_conf)

_loaders['config'] = _loadconfig

def _loadegg(object_type, uri, spec, name, relative_to,
             global_conf):
    loader = EggLoader(spec)
    return loader.get_context(object_type, name, global_conf)

_loaders['egg'] = _loadegg

############################################################
## Loaders
############################################################

class _Loader(object):

    def get_app(self, name=None, global_conf=None):
        return self.app_context(
            name=name, global_conf=global_conf).create()

    def get_filter(self, name=None, global_conf=None):
        return self.filter_context(
            name=naame, global_conf=global_conf).create()

    def get_server(self, name=None, global_conf=None):
        return self.server_context(
            name=naame, global_conf=global_conf).create()

    def app_context(self, name=None, global_conf=None):
        return self.get_context(
            APP, name=name, global_conf=global_conf)

    def filter_context(self, name=None, global_conf=None):
        return self.get_context(
            FILTER, name=name, global_conf=global_conf)

    def server_context(self, name=None, global_conf=None):
        return self.get_context(
            SERVER, name=name, global_conf=global_conf)

    _absolute_re = re.compile(r'^[a-zA-Z]+:')
    def absolute_name(self, name):
        """
        Returns true if the name includes a scheme
        """
        if name is None:
            return False
        return self._absolute_re.search(name)        

class ConfigLoader(_Loader):

    def __init__(self, filename):
        self.filename = filename
        self.parser = RawConfigParser()
        # Don't lower-case keys:
        self.parser.optionxform = str
        # Stupid ConfigParser ignores files that aren't found, so
        # we have to add an extra check:
        if not os.path.exists(filename):
            raise OSError(
                "File %s not found" % filename)
        self.parser.read(filename)

    def get_context(self, object_type, name=None, global_conf=None):
        if self.absolute_name(name):
            return loadcontext(object_type, name,
                               relative_to=os.path.dirname(self.filename),
                               global_conf=global_conf)
        if global_conf is None:
            global_conf = {}
        else:
            global_conf = global_conf.copy()
        section = self.find_config_section(
            object_type, name=name)
        defaults = self.parser.defaults()
        global_conf.update(defaults)
        local_conf = {}
        global_additions = {}
        for option in self.parser.options(section):
            if option.startswith('set '):
                name = option[4:].strip()
                global_additions[name] = global_conf[name] = (
                    self.parser.get(section, option))
            else:
                if option in defaults:
                    # @@: It's a global option (?), so skip it
                    continue
                local_conf[option] = self.parser.get(section, option)
        if 'use' in local_conf:
            use = local_conf.pop('use')
            context = self.get_context(
                object_type, name=use, global_conf=global_conf)
            context.global_conf.update(global_additions)
            context.local_conf.update(local_conf)
            # @@: Should loader be overwritten?
            context.loader = self
            return context
        possible = []
        for protocol_options in object_type.egg_protocols:
            for protocol in protocol_options:
                if protocol in local_conf:
                    possible.append((protocol, local_conf[protocol]))
                    break
        if len(possible) > 1:
            raise LookupError(
                "Multiple protocols given in section %r: %s"
                % (section, possible))
        if not possible:
            raise LookupError(
                "No loader given in section %r" % section)
        found_protocol, found_expr = possible[0]
        del local_conf[found_protocol]
        value = import_string(found_expr)
        context = LoaderContext(
            value, object_type, found_protocol,
            global_conf, local_conf, self)
        return context

    def find_config_section(self, object_type, name=None):
        """
        Return the section name with the given name prefix (following the
        same pattern as ``protocol_desc`` in ``config``.  It must have the
        given name, or for ``'main'`` an empty name is allowed.  The
        prefix must be followed by a ``:``.

        Case is *not* ignored.
        """
        possible = []
        for name_options in object_type.config_prefixes:
            for name_prefix in name_options:
                found = self._find_sections(
                    self.parser.sections(), name_prefix, name)
                if found:
                    possible.extend(found)
                    break
        if not possible:
            raise LookupError(
                "No section %r (prefixed by %s) found in config %s from %s"
                % (name,
                   ' or '.join(map(repr, _flatten(object_type.config_prefixes))),
                   self.filename))
        if len(possible) > 1:
            raise LookupError(
                "Ambiguous section names %r for section %r (prefixed by %s) "
                "found in config %s"
                % (possible, name,
                   ' or '.join(map(repr, _flatten(object_type.config_prefixes))),
                   self.filename))
        return possible[0]

    def _find_sections(self, sections, name_prefix, name):
        found = []
        if name is None:
            if name_prefix in sections:
                found.append(name_prefix)
            name = 'main'
        for section in sections:
            if section.startswith(name_prefix+':'):
                if section[len(name_prefix)+1:].strip() == name:
                    found.append(section)
        return found


class EggLoader(_Loader):

    def __init__(self, spec):
        self.spec = spec

    def get_context(self, object_type, name=None, global_conf=None):
        if self.absolute_name(name):
            return loadcontext(object_type, name,
                               global_conf=global_conf)
        entry_point, protocol = self.find_egg_entry_point(
            object_type, name=name)
        return LoaderContext(
            entry_point,
            object_type,
            protocol,
            global_conf or {}, {},
            self)

    def find_egg_entry_point(self, object_type, name=None):
        """
        Returns the (entry_point, protocol) for the with the given
        ``name``.
        """
        if name is None:
            name = 'main'
        possible = []
        for protocol_options in object_type.egg_protocols:
            for protocol in protocol_options:
                entry = pkg_resources.get_entry_info(
                    self.spec,
                    protocol,
                    name)
                if entry is not None:
                    possible.append((entry.load(), protocol))
                    break
        if not possible:
            # Better exception
            raise LookupError(
                "Entry point %r not found in egg %r (protocols: %s)"
                % (name, self.spec,
                   ', '.join(_flatten(object_type.egg_protocols))))
        if len(possible) > 1:
            raise LookupError(
                "Ambiguous entry points for %r in egg %r (protocols: %s)"
                % (name, self.spec, ', '.join(_flatten(protocol_list))))
        return possible[0]

class LoaderContext(object):

    def __init__(self, obj, object_type, protocol,
                 global_conf, local_conf, loader):
        self.object = obj
        self.object_type = object_type
        self.protocol = protocol
        self.global_conf = global_conf
        self.local_conf = local_conf
        self.loader = loader

    def create(self):
        return self.object_type.invoke(self)
