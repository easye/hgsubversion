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

import os
import sys
import traceback

from mercurial import commands
from mercurial import extensions
from mercurial import help
from mercurial import hg
from mercurial import templatekw
from mercurial import util as hgutil
from mercurial import demandimport
demandimport.ignore.extend([
    'svn',
    'svn.client',
    'svn.core',
    'svn.delta',
    'svn.ra',
    ])

import svncommands
import util
import svnrepo
import wrappers

svnopts = [
    ('', 'stupid', None,
     'use slower, but more compatible, protocol for Subversion'),
]

# generic means it picks up all options from svnopts
# fixdoc means update the docstring
# TODO: fixdoc hoses l18n
wrapcmds = { # cmd: generic, target, fixdoc, ppopts, opts
    'parents': (False, None, False, False, [
        ('', 'svn', None, 'show parent svn revision instead'),
    ]),
    'diff': (False, None, False, False, [
        ('', 'svn', None, 'show svn diffs against svn parent'),
    ]),
    'pull': (True, 'sources', True, True, []),
    'push': (True, 'destinations', True, True, []),
    'incoming': (False, 'sources', True, True, []),
    'version': (False, None, False, False, [
        ('', 'svn', None, 'print hgsubversion information as well')]),
    'clone': (False, 'sources', True, True, [
        ('T', 'tagpaths', '',
         'list of paths to search for tags in Subversion repositories'),
        ('A', 'authors', '',
         'file mapping Subversion usernames to Mercurial authors'),
        ('', 'filemap', '',
         'file containing rules for remapping Subversion repository paths'),
        ('', 'layout', 'auto', ('import standard layout or single '
                                'directory? Can be standard, single, or auto.')),
        ('', 'branchmap', '', 'file containing rules for branch conversion'),
        ('', 'startrev', '', ('convert Subversion revisions starting at the one '
                              'specified, either an integer revision or HEAD; '
                              'HEAD causes only the latest revision to be '
                              'pulled')),
    ]),
}


# only need the discovery variant of this code when we drop hg < 1.6
try:
    from mercurial import discovery
    def findoutgoing(orig, *args, **opts):
        capable = getattr(args[1], 'capable', lambda x: False)
        if capable('subversion'):
            return wrappers.outgoing(*args, **opts)
        else:
            return orig(*args, **opts)
    extensions.wrapfunction(discovery, 'findoutgoing', findoutgoing)
except ImportError:
    pass

def uisetup(ui):
    """insert command wrappers for a bunch of commands"""
    docvals = {'extension': 'hgsubversion'}
    for cmd, (generic, target, fixdoc, ppopts, opts) in wrapcmds.iteritems():

        if fixdoc and wrappers.generic.__doc__:
            docvals['command'] = cmd
            docvals['Command'] = cmd.capitalize()
            docvals['target'] = target
            doc = wrappers.generic.__doc__.strip() % docvals
            fn = getattr(commands, cmd)
            fn.__doc__ = fn.__doc__.rstrip() + '\n\n    ' + doc

        wrapped = generic and wrappers.generic or getattr(wrappers, cmd)
        entry = extensions.wrapcommand(commands.table, cmd, wrapped)
        if ppopts:
            entry[1].extend(svnopts)
        if opts:
            entry[1].extend(opts)

    try:
        rebase = extensions.find('rebase')
        if not rebase:
            return
        entry = extensions.wrapcommand(rebase.cmdtable, 'rebase', wrappers.rebase)
        entry[1].append(('', 'svn', None, 'automatic svn rebase'))
    except:
        pass

    helpdir = os.path.join(os.path.dirname(__file__), 'help')

    entries = (
        (['subversion'],
         "Working with Subversion Repositories",
         lambda: open(os.path.join(helpdir, 'subversion.rst')).read()),
    )

    # in 1.6 and earler the help table is a tuple
    if getattr(help.helptable, 'extend', None):
        help.helptable.extend(entries)
    else:
        help.helptable = help.helptable + entries


def _get_svnmeta(ctx):
    """Returns a dictionary with parsed convert_revision or None."""
    unparsed = ctx.extra().get('convert_revision', '')
    if unparsed.startswith('svn:'):
        unparsed = unparsed[4:]  # remove "svn:"
        return {'svnuuid': unparsed[:36],
                'svnpath': unparsed[36:].rsplit('@', 1)[0],
                'svnrev': unparsed[36:].rsplit('@', 1)[-1]}
    return None


def _show_tpl_kw(ctx, kw):
    convinfo = _get_svnmeta(ctx)
    if convinfo is None:
        return ''
    return convinfo.get(kw, '')


templatekw.keywords.update({
    'svnrev': lambda repo, ctx, templ, **a: _show_tpl_kw(ctx, 'svnrev'),
    'svnpath': lambda repo, ctx, templ, **a: _show_tpl_kw(ctx, 'svnpath'),
    'svnuuid': lambda repo, ctx, templ, **a: _show_tpl_kw(ctx, 'svnuuid'),
    })


def reposetup(ui, repo):
    if repo.local():
       svnrepo.generate_repo_class(ui, repo)

_old_local = hg.schemes['file']
def _lookup(url):
    if util.islocalrepo(url):
        return svnrepo
    else:
        return _old_local(url)

# install scheme handlers
hg.schemes.update({ 'file': _lookup, 'http': svnrepo, 'https': svnrepo,
                    'svn': svnrepo, 'svn+ssh': svnrepo, 'svn+http': svnrepo,
                    'svn+https': svnrepo})

commands.optionalrepo += ' svn'

cmdtable = {
    "svn":
        (svncommands.svn,
         [('u', 'svn-url', '', 'path to the Subversion server.'),
          ('', 'stupid', False, 'be stupid and use diffy replay.'),
          ('A', 'authors', '', 'username mapping filename'),
          ('', 'filemap', '',
           'remap file to exclude paths or include only certain paths'),
          ('', 'force', False, 'force an operation to happen'),
          ('', 'username', '', 'username for authentication'),
          ('', 'password', '', 'password for authentication'),
          ('r', 'rev', '', 'Mercurial revision'),
          ],
         'hg svn <subcommand> ...',
         ),
}

# only these methods are public
__all__ = ('cmdtable', 'reposetup', 'uisetup')
