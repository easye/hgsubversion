'''integration with Subversion repositories

hgsubversion is an extension for Mercurial that allows it to act as a Subversion
client, offering fast, incremental and bidirectional synchronisation.

At this point, hgsubversion is usable by users reasonably familiar with
Mercurial as a VCS. It's not recommended to dive into hgsubversion as an
introduction to Mercurial, since hgsubversion "bends the rules" a little
and violates some of the typical assumptions of early Mercurial users.

Before using hgsubversion, we *strongly* encourage running the
automated tests. See 'README' in the hgsubversion directory for
details.

For more information and instructions, see :hg:`help subversion`.
'''

testedwith = '3.7 3.8 3.9 4.0 4.1 4.2 4.3 4.4 4.5 4.6 4.7 4.8'

import inspect
import os

from mercurial import commands
from mercurial import exchange
from mercurial import error as hgerror
from mercurial import extensions
from mercurial import help
from mercurial import hg
from mercurial import localrepo
from mercurial import util as hgutil
try:
    from mercurial import demandimport
    demandimport.ignore.extend([
        'svn',
        'svn.client',
        'svn.core',
        'svn.delta',
        'svn.ra',
    ])
except (ImportError, AttributeError):
    from hgdemandimport import demandimport
    demandimport.ignores |= {
        'svn',
        'svn.client',
        'svn.core',
        'svn.delta',
        'svn.ra',
    }

from mercurial import discovery
from mercurial import revset
from mercurial import subrepo

from . import svncommands
from . import util
from . import svnrepo
from . import wrappers
from . import svnexternals
from . import compathacks

svnopts = [
    ('', 'stupid', None,
     'use slower, but more compatible, protocol for Subversion'),
]

# generic means it picks up all options from svnopts
# fixdoc means update the docstring
# TODO: fixdoc hoses l18n
wrapcmds = { # cmd: generic, target, fixdoc, ppopts, opts
    b'parents': (False, None, False, False, [
        ('', b'svn', None, 'show parent svn revision instead'),
    ]),
    b'diff': (False, None, False, False, [
        (b'', b'svn', None, 'show svn diffs against svn parent'),
    ]),
    b'pull': (True, b'sources', True, True, []),
    b'push': (True, b'destinations', True, True, []),
    b'incoming': (False, b'sources', True, True, []),
    b'version': (False, None, False, False, [
        (b'', b'svn', None, 'print hgsubversion information as well')]),
    b'clone': (False, b'sources', True, True, [
        ('T', 'tagpaths', '',
         'list of paths to search for tags in Subversion repositories'),
        ('', 'branchdir', '',
         'path to search for branches in subversion repositories'),
        ('', 'trunkdir', '',
         'path to trunk in subversion repositories'),
        ('', 'infix', '',
         'path relative to trunk, branch an tag dirs to import'),
        ('A', 'authors', '',
         'file mapping Subversion usernames to Mercurial authors'),
        ('', 'filemap', '',
         'file containing rules for remapping Subversion repository paths'),
        ('', 'layout', 'auto', ('import standard layout or single '
                                'directory? Can be standard, single, or auto.')),
        ('', 'branchmap', '', 'file containing rules for branch conversion'),
        ('', 'tagmap', '', 'file containing rules for renaming tags'),
        ('', 'startrev', '', ('convert Subversion revisions starting at the one '
                              'specified, either an integer revision or HEAD; '
                              'HEAD causes only the latest revision to be '
                              'pulled')),
    ]),
}

def findcommonoutgoing(orig, *args, **opts):
    capable = getattr(args[1], 'capable', lambda x: False)
    if capable('subversion'):
        return wrappers.findcommonoutgoing(*args, **opts)
    else:
        return orig(*args, **opts)
extensions.wrapfunction(discovery, 'findcommonoutgoing', findcommonoutgoing)

def extsetup(ui):
    """insert command wrappers for a bunch of commands"""

    docvals = {'extension': 'hgsubversion'}
    for cmd, (generic, target, fixdoc, ppopts, opts) in iter(wrapcmds.items()):

        if fixdoc and wrappers.generic.__doc__:
            docvals['command'] = cmd
            docvals['Command'] = cmd.capitalize()
            docvals['target'] = target
            doc = wrappers.generic.__doc__.strip() % docvals
            fn = getattr(commands, cmd.decode())
            fn.__doc__ = fn.__doc__.rstrip() + '\n\n    ' + doc

        wrapped = generic and wrappers.generic or getattr(wrappers, cmd.decode())
        entry = extensions.wrapcommand(commands.table, cmd, wrapped)
        if ppopts:
            entry[1].extend(svnopts)
        if opts:
            entry[1].extend(opts)

    try:
        rebase = extensions.find('rebase')
        if not rebase:
            return
        entry = extensions.wrapcommand(rebase.cmdtable, b'rebase', wrappers.rebase)
        entry[1].append((b'', b'svn', None, 'automatic svn rebase'))
    except:
        pass

    extensions.wrapfunction(exchange, 'push', wrappers.exchangepush)
    extensions.wrapfunction(exchange, 'pull', wrappers.exchangepull)

    helpdir = os.path.join(os.path.dirname(__file__), 'help')

    entries = (
        (['subversion'],
         "Working with Subversion Repositories",
         # Mercurial >= 3.6: doc(ui)
         lambda *args: open(os.path.join(helpdir, 'subversion.rst')).read()),
    )

    help.helptable.extend(entries)

    revset.symbols.update(util.revsets)

    subrepo.types['hgsubversion'] = svnexternals.svnsubrepo

def reposetup(ui, repo):
    if repo.local():
        svnrepo.generate_repo_class(ui, repo)
        for tunnel in ui.configlist('hgsubversion', 'tunnels'):
            hg.schemes['svn+' + tunnel] = svnrepo

    if ui.configbool('hgsubversion', 'nativerevs'):
        extensions.wrapfunction(revset, 'stringset', util.revset_stringset)
        revset.symbols['stringset'] = revset.stringset
        revset.methods['string'] = revset.stringset
        revset.methods['symbol'] = revset.stringset

_old_local = hg.schemes[b'file']
def _lookup(url):
    if util.islocalrepo(url):
        return svnrepo
    else:
        return _old_local(url)

# install scheme handlers
hg.schemes.update({ 'file': _lookup, 'http': svnrepo, 'https': svnrepo,
                    'svn': svnrepo, 'svn+ssh': svnrepo, 'svn+http': svnrepo,
                    'svn+https': svnrepo})

if hgutil.safehasattr(commands, 'optionalrepo'):
    commands.optionalrepo += ' svn'

svncommandopts = [
    (b'u', b'svn-url', b'', b'path to the Subversion server.'),
    (b'', b'stupid', False, b'be stupid and use diffy replay.'),
    (b'A', b'authors', b'', b'username mapping filename'),
    (b'', b'filemap', b'',
     b'remap file to exclude paths or include only certain paths'),
    (b'', b'force', False, b'force an operation to happen'),
    (b'', b'username', b'', b'username for authentication'),
    (b'', b'password', b'', b'password for authentication'),
    (b'r', b'rev', [], b'Mercurial revision'),
    (b'', b'unsafe-skip-uuid-check', False,
     b'skip repository uuid check in rebuildmeta'),
]
svnusage = b'hg svn <subcommand> ...'

# only these methods are public
__all__ = (b'cmdtable', b'reposetup', b'uisetup')

# set up commands and templatekeywords (written this way to maintain backwards
# compatibility until we drop support for 3.7 for templatekeywords and 4.3 for
# commands)
cmdtable = {
    b"svn": (svncommands.svn, svncommandopts, svnusage),
}
configtable = {}
try:
    from mercurial import registrar
    templatekeyword = registrar.templatekeyword()
    loadkeyword = lambda registrarobj: None  # no-op

    if hgutil.safehasattr(registrar, 'command'):
        cmdtable = {}
        command = registrar.command(cmdtable)
        @command(b'svn', svncommandopts, svnusage)
        def svncommand(*args, **kwargs):
            return svncommands.svn(*args, **kwargs)

    if hgutil.safehasattr(registrar, 'configitem'):
        configitem = registrar.configitem(configtable)
    else:
        def configitem(*args, **kwargs):
            pass
except (ImportError, AttributeError):
    # registrar.templatekeyword isn't available = loading by old hg

    templatekeyword = compathacks._funcregistrarbase()
    templatekeyword._docformat = ":%s: %s"

    # minimum copy from templatekw.loadkeyword
    def loadkeyword(registrarobj):
        from mercurial import templatekw
        for name, func in registrarobj._table.iteritems():
            templatekw.keywords[name] = func

    def configitem(*args, **kwargs):
        # no-op so we can put config items at the top level instead of
        # deeply nested
        pass

if not hgutil.safehasattr(configitem, 'dynamicdefault'):
    # hg 4.3 lacks support for dynamicdefault in a way that means we
    # have to not use the config registrar at all.
    def configitem(*args, **kwargs):
        pass
    configitem.dynamicdefault = None

# real default is 'svnexternals'. Can also be 'subrepos' or
# 'ignore'. Defines how to handle svn:externals.
configitem(b'hgsubversion', b'externals', default=configitem.dynamicdefault)

# If true, use diff+patch instead of svn native replay RPC.
configitem(b'hgsubversion', b'stupid', default=False)

# Allows configuring extra of svn+$SCHEME tunnel protocols
configitem(b'hgsubversion', b'tunnels', default=list)
# If true, monkeypatch revset parser to allow r123 to map to
# Subversion revision 123.
configitem(b'hgsubversion', b'nativerevs', default=False)

# Auth config for the Subversion backend
configitem(b'hgsubversion', b'username', default='')
configitem(b'hgsubversion', b'password', default='')
# The default value of the empty list means to use a default set of
# password stores. The specific ones that will be consulted depend on
# the compile-time options of your Subversion libraries.
configitem(b'hgsubversion', b'password_stores', default=list)

# real default is None
configitem(b'hgsubversion', b'revmapimpl', default=configitem.dynamicdefault)
# real default is 'auto'
configitem(b'hgsubversion', b'layout', default=configitem.dynamicdefault)

# real default is True
configitem(b'hgsubversion', b'defaultauthors', default=configitem.dynamicdefault)
# real default is False
configitem(b'hgsubversion', b'caseignoreauthors', default=configitem.dynamicdefault)
# real default is None
configitem(b'hgsubversion', b'mapauthorscmd', default=configitem.dynamicdefault)
# Defaults to the UUID identifying the source svn repo.
configitem(b'hgsubversion', b'defaulthost', default=configitem.dynamicdefault)
# real default is True
configitem(b'hgsubversion', b'usebranchnames', default=configitem.dynamicdefault)
# real default is ''
configitem(b'hgsubversion', b'defaultmessage', default=configitem.dynamicdefault)
# real default is ''
configitem(b'hgsubversion', b'branch', default=configitem.dynamicdefault)
# real default is ['tags']
configitem(b'hgsubversion', b'taglocations', default=configitem.dynamicdefault)
# real default is 'trunk'
configitem(b'hgsubversion', b'trunkdir', default=configitem.dynamicdefault)
# real default is ''
configitem(b'hgsubversion', b'infix', default=configitem.dynamicdefault)
# real default is ''
configitem(b'hgsubversion', b'unsafeskip', default=configitem.dynamicdefault)
# real default is ['tags']
configitem(b'hgsubversion', b'tagpaths', default=configitem.dynamicdefault)
# real default is 'branches'
configitem(b'hgsubversion', b'branchdir', default=configitem.dynamicdefault)
# real default is 200
configitem(b'hgsubversion', b'filestoresize', default=configitem.dynamicdefault)
# Typically unset, custom location of map files typically stored inside .hg
configitem(b'hgsubversion', b'filemap', default=None)
configitem(b'hgsubversion', b'branchmap', default=None)
configitem(b'hgsubversion', b'authormap', default=None)
configitem(b'hgsubversion', b'tagmap', default=None)
# real default is False
configitem(b'hgsubversion', b'failoninvalidreplayfile',
           default=configitem.dynamicdefault)
# real default is 0
configitem(b'hgsubversion', b'startrev', default=configitem.dynamicdefault)
# extra pragmas to feed to sqlite revmap implementation
configitem(b'hgsubversion', b'sqlitepragmas', default=list)
# real default is False
configitem(b'hgsubversion', b'failonmissing', default=configitem.dynamicdefault)
# svn:externals support
configitem(b'subrepos', b'hgsubversion:allowed', default=False)

def _templatehelper(ctx, kw):
    '''
    Helper function for displaying information about converted changesets.
    '''
    convertinfo = util.getsvnrev(ctx, '')

    if not convertinfo or not convertinfo.startswith('svn:'):
        return ''

    if kw == 'svnuuid':
        return convertinfo[4:40]
    elif kw == 'svnpath':
        return convertinfo[40:].rsplit('@', 1)[0]
    elif kw == 'svnrev':
        return convertinfo[40:].rsplit('@', 1)[-1]
    else:
        raise hgerror.Abort('unrecognized hgsubversion keyword %s' % kw)

_ishg48 = 'requires' in inspect.getargspec(
    getattr(templatekeyword, '_extrasetup', lambda: None)).args

if _ishg48:
    @templatekeyword(b'svnrev', requires={b'ctx'})
    def svnrevkw(context, mapping):
        """:svnrev: String. Converted subversion revision number."""
        ctx = context.resource(mapping, b'ctx')
        return _templatehelper(ctx, b'svnrev')

    @templatekeyword(b'svnpath', requires={b'ctx'})
    def svnpathkw(context, mapping):
        """:svnpath: String. Converted subvenrsion revision project path."""
        ctx = context.resource(mapping, b'ctx')
        return _templatehelper(ctx, b'svnpath')

    @templatekeyword(b'svnuuid', requires={b'ctx'})
    def svnuuidkw(context, mapping):
        """:svnuuid: String. Converted subversion revision repository identifier."""
        ctx = context.resource(mapping, b'ctx')
        return _templatehelper(ctx, b'svnuuid')
else:
    @templatekeyword(b'svnrev')
    def svnrevkw(**args):
        """:svnrev: String. Converted subversion revision number."""
        return _templatehelper(args[b'ctx'], b'svnrev')

    @templatekeyword(b'svnpath')
    def svnpathkw(**args):
        """:svnpath: String. Converted subversion revision project path."""
        return _templatehelper(args[b'ctx'], b'svnpath')

    @templatekeyword(b'svnuuid')
    def svnuuidkw(**args):
        """:svnuuid: String. Converted subversion revision repository identifier."""
        return _templatehelper(args[b'ctx'], b'svnuuid')

loadkeyword(templatekeyword)
