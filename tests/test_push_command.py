import test_util

import atexit
import errno
import os
import sys
import random
import shutil
import socket
import subprocess
import unittest

from mercurial import context
from mercurial import commands
from mercurial import error as hgerror
from mercurial import hg
from mercurial import node
from mercurial import revlog
from mercurial import util as hgutil

from hgsubversion import util
from hgsubversion import compathacks

import time

revsymbol = test_util.revsymbol

try:
    lookuperror = revlog.LookupError
except AttributeError:
    lookuperror = hgerror.LookupError

class PushTests(test_util.TestBase):
    obsolete_mode_tests = True

    def setUp(self):
        test_util.TestBase.setUp(self)
        self.repo_path = self.load_and_fetch('simple_branch.svndump')[1]

    def test_cant_push_empty_ctx(self):
        repo = self.repo
        def file_callback(repo, memctx, path):
            if path == 'adding_file':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo',
                                                  islink=False,
                                                  isexec=False,
                                                  copied=False)
            raise IOError()
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             [],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        old_tip = revsymbol(repo, 'tip').node()
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertEqual(tip.node(), old_tip)

    def test_push_add_of_added_upstream_gives_sane_error(self):
        repo = self.repo
        def file_callback(repo, memctx, path):
            if path == 'adding_file':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo',
                                                  islink=False,
                                                  isexec=False,
                                                  copied=False)
            raise IOError()
        p1 = revsymbol(repo, 'default').node()
        ctx = context.memctx(repo,
                             (p1, node.nullid),
                             'automated test',
                             ['adding_file'],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        old_tip = revsymbol(repo, 'tip').node()
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), old_tip)

        # This node adds the same file as the first one we added, and
        # will be refused by the server for adding a file that already
        # exists. We should respond with an error suggesting the user
        # rebase.
        ctx = context.memctx(repo,
                             (p1, node.nullid),
                             'automated test',
                             ['adding_file'],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        old_tip = revsymbol(repo, 'tip').node()
        try:
          self.pushrevisions()
        except hgerror.Abort, e:
          assert "pull again and rebase" in str(e)
        tip = revsymbol(self.repo, 'tip')
        self.assertEqual(tip.node(), old_tip)

    def test_cant_push_with_changes(self):
        repo = self.repo
        def file_callback(repo, memctx, path):
            return compathacks.makememfilectx(repo,
                                              memctx=memctx,
                                              path=path,
                                              data='foo',
                                              islink=False,
                                              isexec=False,
                                              copied=False)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             ['adding_file'],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        # Touch an existing file
        repo.wwrite('beta', 'something else', '')
        try:
            self.pushrevisions()
        except hgerror.Abort:
            pass
        tip = revsymbol(self.repo, 'tip')
        self.assertEqual(new_hash, tip.node())

    def internal_push_over_svnserve(self, subdir='', commit=True):
        repo_path = self.load_svndump('simple_branch.svndump')
        open(os.path.join(repo_path, 'conf', 'svnserve.conf'),
             'w').write('[general]\nanon-access=write\n[sasl]\n')
        self.port = random.randint(socket.IPPORT_USERRESERVED, 65535)
        self.host = socket.gethostname()

        # The `svnserve` binary appears to use the obsolete `gethostbyname(3)`
        # function, which always returns an IPv4 address, even on hosts that
        # support and expect IPv6. As a workaround, resolve the hostname
        # within the test harness with `getaddrinfo(3)` to ensure that the
        # client and server both use the same IPv4 or IPv6 address.
        try:
            addrinfo = socket.getaddrinfo(self.host, self.port)
        except socket.gaierror as e:
            # gethostname() can give a hostname that doesn't
            # resolve. Seems bad, but let's fall back to `localhost` in
            # that case and hope for the best.
            self.host = 'localhost'
            addrinfo = socket.getaddrinfo(self.host, self.port)
        # On macOS svn seems to have issues with IPv6 at least some of
        # the time, so try and bias towards IPv4. This works because
        # AF_INET is less than AF_INET6 on all platforms I've
        # checked. Hopefully any platform where that's not true will
        # be fine with IPv6 all the time. :)
        selected = sorted(addrinfo)[0]
        self.host = selected[4][0]

        # If we're connecting via IPv6 the need to put brackets around the
        # hostname in the URL.
        ipv6 = selected[0] == socket.AF_INET6

        # Ditch any interface information since that's not helpful in
        # a URL
        if ipv6 and ':' in self.host and '%' in self.host:
            self.host = self.host.rsplit('%', 1)[0]

        urlfmt = 'svn://[%s]:%d/%s' if ipv6 else 'svn://%s:%d/%s'

        args = ['svnserve', '--daemon', '--foreground',
                '--listen-port=%d' % self.port,
                '--listen-host=%s' % self.host,
                '--root=%s' % repo_path]

        svnserve = subprocess.Popen(args, stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT)
        self.svnserve_pid = svnserve.pid
        try:
            time.sleep(2)
            import shutil
            shutil.rmtree(self.wc_path)
            commands.clone(self.ui(),
                           urlfmt % (self.host, self.port, subdir),
                           self.wc_path, noupdate=True)

            repo = self.repo
            old_tip = revsymbol(repo, 'tip').node()
            expected_parent = revsymbol(repo, 'default').node()
            def file_callback(repo, memctx, path):
                if path == 'adding_file':
                    return compathacks.makememfilectx(repo,
                                                      memctx=memctx,
                                                      path=path,
                                                      data='foo',
                                                      islink=False,
                                                      isexec=False,
                                                      copied=False)
                raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
            ctx = context.memctx(repo,
                                 parents=(revsymbol(repo, 'default').node(), node.nullid),
                                 text='automated test',
                                 files=['adding_file'],
                                 filectxfn=file_callback,
                                 user='an_author',
                                 date='2008-10-07 20:59:48 -0500',
                                 extra={'branch': 'default', })
            new_hash = repo.commitctx(ctx)
            if not commit:
                return # some tests use this test as an extended setup.
            hg.update(repo, revsymbol(repo, 'tip').node())
            oldauthor = revsymbol(repo, 'tip').user()
            commands.push(repo.ui, repo)
            tip = revsymbol(self.repo, 'tip')
            self.assertNotEqual(oldauthor, tip.user())
            self.assertNotEqual(tip.node(), old_tip)
            self.assertEqual(tip.parents()[0].node(), expected_parent)
            self.assertEqual(tip['adding_file'].data(), 'foo')
            self.assertEqual(tip.branch(), 'default')
            # unintended behaviour:
            self.assertNotEqual('an_author', tip.user())
            self.assertEqual('(no author)', tip.user().rsplit('@', 1)[0])
        finally:
            if sys.version_info >= (2,6):
                svnserve.kill()
            else:
                test_util.kill_process(svnserve)

    def test_push_over_svnserve(self):
        self.internal_push_over_svnserve()

    def test_push_over_svnserve_with_subdir(self):
        self.internal_push_over_svnserve(subdir='///branches////the_branch/////')

    def test_push_to_default(self, commit=True):
        repo = self.repo
        old_tip = revsymbol(repo, 'tip').node()
        expected_parent = revsymbol(repo, 'default').node()
        def file_callback(repo, memctx, path):
            if path == 'adding_file':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo',
                                                  islink=False,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             ['adding_file'],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        if not commit:
            return # some tests use this test as an extended setup.
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), old_tip)
        self.assertEqual(node.hex(tip.parents()[0].node()),
                         node.hex(expected_parent))
        self.assertEqual(tip['adding_file'].data(), 'foo')
        self.assertEqual(tip.branch(), 'default')

    def test_push_two_revs_different_local_branch(self):
        def filectxfn(repo, memctx, path):
            return compathacks.makememfilectx(repo,
                                              memctx=memctx,
                                              path=path,
                                              data=path,
                                              islink=False,
                                              isexec=False,
                                              copied=False)
        oldtiphash = revsymbol(self.repo, 'default').node()
        lr = self.repo
        ctx = context.memctx(lr,
                             (lr[0].node(), revlog.nullid,),
                             'automated test',
                             ['gamma', ],
                             filectxfn,
                             'testy',
                             '2008-12-21 16:32:00 -0500',
                             {'branch': 'localbranch', })
        newhash = lr.commitctx(ctx)
        ctx = context.memctx(lr,
                             (newhash, revlog.nullid),
                             'automated test2',
                             ['delta', ],
                             filectxfn,
                             'testy',
                             '2008-12-21 16:32:00 -0500',
                             {'branch': 'localbranch', })
        newhash = lr.commitctx(ctx)
        repo = self.repo
        hg.update(repo, newhash)
        commands.push(repo.ui, repo)
        self.assertEqual(revsymbol(self.repo, 'tip').parents()[0].parents()[0].node(), oldtiphash)
        self.assertEqual(revsymbol(self.repo, 'tip').files(), ['delta', ])
        self.assertEqual(sorted(revsymbol(self.repo, 'tip').manifest().keys()),
                         ['alpha', 'beta', 'delta', 'gamma'])

    def test_push_two_revs(self):
        # set up some work for us
        self.test_push_to_default(commit=False)
        repo = self.repo
        old_tip = revsymbol(repo, 'tip').node()
        expected_parent = revsymbol(repo, 'tip').parents()[0].node()
        def file_callback(repo, memctx, path):
            if path == 'adding_file2':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo2',
                                                  islink=False,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             ['adding_file2'],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), old_tip)
        self.assertNotEqual(tip.parents()[0].node(), old_tip)
        self.assertEqual(tip.parents()[0].parents()[0].node(), expected_parent)
        self.assertEqual(tip['adding_file2'].data(), 'foo2')
        self.assertEqual(tip['adding_file'].data(), 'foo')
        self.assertEqual(tip.parents()[0]['adding_file'].data(), 'foo')
        try:
            self.assertEqual(tip.parents()[0]['adding_file2'].data(), 'foo')
            assert False, "this is impossible, adding_file2 should not be in this manifest."
        except lookuperror, e:
            pass
        self.assertEqual(tip.branch(), 'default')

    def test_push_to_branch(self, push=True):
        repo = self.repo
        def file_callback(repo, memctx, path):
            if path == 'adding_file':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo',
                                                  islink=False,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'the_branch').node(), node.nullid),
                             'automated test',
                             ['adding_file'],
                             file_callback,
                             'an_author',
                             '2008-10-07 20:59:48 -0500',
                             {'branch': 'the_branch', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        if push:
            self.pushrevisions()
            tip = revsymbol(self.repo, 'tip')
            self.assertNotEqual(tip.node(), new_hash)
            self.assertEqual(tip['adding_file'].data(), 'foo')
            self.assertEqual(tip.branch(), 'the_branch')

    def test_push_to_non_tip(self):
        self.test_push_to_branch(push=False)
        wc2path = self.wc_path + '_clone'
        u = self.repo.ui
        test_util.hgclone(self.repo.ui, self.wc_path, wc2path, update=False)
        res = self.pushrevisions()
        self.assertEqual(0, res)
        oldf = open(os.path.join(self.wc_path, '.hg', 'hgrc'))
        hgrc = oldf.read()
        oldf.close()
        shutil.rmtree(self.wc_path)
        test_util.hgclone(u, wc2path, self.wc_path, update=False)
        oldf = open(os.path.join(self.wc_path, '.hg', 'hgrc'), 'w')
        oldf.write(hgrc)
        oldf.close()

        # do a commit here
        self.commitchanges([('foobaz', 'foobaz', 'This file is added on default.',),
                            ],
                           parent='default',
                           message='commit to default')
        from hgsubversion import svncommands
        svncommands.rebuildmeta(u,
                                self.repo,
                                args=[test_util.fileurl(self.repo_path)])


        hg.update(self.repo, revsymbol(self.repo, 'tip').node())
        oldnode = revsymbol(self.repo, 'tip').hex()
        self.pushrevisions(expected_extra_back=1)
        self.assertNotEqual(oldnode, revsymbol(self.repo, 'tip').hex(), 'Revision was not pushed.')

    def test_delete_file(self):
        repo = self.repo
        def file_callback(repo, memctx, path):
            return compathacks.filectxfn_deleted(memctx, path)
        old_files = set(revsymbol(repo, 'default').manifest().keys())
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             ['alpha'],
                             file_callback,
                             'an author',
                             '2008-10-29 21:26:00 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertEqual(old_files,
                         set(tip.manifest().keys() + ['alpha']))
        self.assert_('alpha' not in tip.manifest())

    def test_push_executable_file(self):
        self.test_push_to_default(commit=True)
        repo = self.repo
        def file_callback(repo, memctx, path):
            if path == 'gamma':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo',
                                                  islink=False,
                                                  isexec=True,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'tip').node(), node.nullid),
                             'message',
                             ['gamma', ],
                             file_callback,
                             'author',
                             '2008-10-29 21:26:00 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.clean(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), new_hash)
        self.assert_('@' in revsymbol(self.repo, 'tip').user())
        self.assertEqual(tip['gamma'].flags(), 'x')
        self.assertEqual(tip['gamma'].data(), 'foo')
        self.assertEqual(sorted([x for x in tip.manifest().keys() if 'x' not in
                                tip[x].flags()]),
                         ['adding_file', 'alpha', 'beta', ])

    def test_push_symlink_file(self):
        self.test_push_to_default(commit=True)
        repo = self.repo
        def file_callback(repo, memctx, path):
            if path == 'gamma':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='foo',
                                                  islink=True,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'tip').node(), node.nullid),
                             'message',
                             ['gamma', ],
                             file_callback,
                             'author',
                             '2008-10-29 21:26:00 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        # grab a new repo instance (self.repo is an @property functions)
        repo = self.repo
        tip = revsymbol(repo, 'tip')
        self.assertNotEqual(tip.node(), new_hash)
        self.assertEqual(tip['gamma'].flags(), 'l')
        self.assertEqual(tip['gamma'].data(), 'foo')
        self.assertEqual(sorted([x for x in tip.manifest().keys() if 'l' not in
                                 tip[x].flags()]),
                         ['adding_file', 'alpha', 'beta', ])

        def file_callback2(repo, memctx, path):
            if path == 'gamma':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='a' * 129,
                                                  islink=True,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)

        ctx = context.memctx(repo,
                             (revsymbol(repo, 'tip').node(), node.nullid),
                             'message',
                             ['gamma', ],
                             file_callback2,
                             'author',
                             '2014-08-08 20:11:41 -0700',
                             {'branch': 'default', })
        repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        # grab a new repo instance (self.repo is an @property functions)
        repo = self.repo
        tip = revsymbol(repo, 'tip')
        self.assertEqual(tip['gamma'].flags(), 'l')
        self.assertEqual(tip['gamma'].data(), 'a'*129)

        def file_callback3(repo, memctx, path):
            if path == 'gamma':
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data='a' * 64 + 'b' * 65,
                                                  islink=True,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)

        ctx = context.memctx(repo,
                             (revsymbol(repo, 'tip').node(), node.nullid),
                             'message',
                             ['gamma', ],
                             file_callback3,
                             'author',
                             '2014-08-08 20:16:25 -0700',
                             {'branch': 'default', })
        repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        repo = self.repo
        tip = revsymbol(repo, 'tip')
        self.assertEqual(tip['gamma'].flags(), 'l')
        self.assertEqual(tip['gamma'].data(), 'a' * 64 + 'b' * 65)


    def test_push_existing_file_newly_symlink(self):
        self.test_push_existing_file_newly_execute(execute=False,
                                                   link=True,
                                                   expected_flags='l')

    def test_push_existing_file_newly_execute(self, execute=True,
                                              link=False, expected_flags='x'):
        self.test_push_to_default()
        repo = self.repo
        def file_callback(repo, memctx, path):
            return compathacks.makememfilectx(repo,
                                              memctx=memctx,
                                              path=path,
                                              data='foo',
                                              islink=link,
                                              isexec=execute,
                                              copied=False)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'message',
                             ['alpha', ],
                             file_callback,
                             'author',
                             '2008-1-1 00:00:00 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), new_hash)
        self.assertEqual(tip['alpha'].data(), 'foo')
        self.assertEqual(tip.parents()[0]['alpha'].flags(), '')
        self.assertEqual(tip['alpha'].flags(), expected_flags)
        # while we're here, double check pushing an already-executable file
        # works
        repo = self.repo
        def file_callback2(repo, memctx, path):
            return compathacks.makememfilectx(repo,
                                              memctx=memctx,
                                              path=path,
                                              data='bar',
                                              islink=link,
                                              isexec=execute,
                                              copied=False)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'mutate already-special file alpha',
                             ['alpha', ],
                             file_callback2,
                             'author',
                             '2008-1-1 00:00:00 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), new_hash)
        self.assertEqual(tip['alpha'].data(), 'bar')
        self.assertEqual(tip.parents()[0]['alpha'].flags(), expected_flags)
        self.assertEqual(tip['alpha'].flags(), expected_flags)
        # now test removing the property entirely
        repo = self.repo
        def file_callback3(repo, memctx, path):
            return compathacks.makememfilectx(repo,
                                              memctx=memctx,
                                              path=path,
                                              data='bar',
                                              islink=False,
                                              isexec=False,
                                              copied=False)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'convert alpha back to regular file',
                             ['alpha', ],
                             file_callback3,
                             'author',
                             '2008-01-01 00:00:00 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), new_hash)
        self.assertEqual(tip['alpha'].data(), 'bar')
        self.assertEqual(tip.parents()[0]['alpha'].flags(), expected_flags)
        self.assertEqual(tip['alpha'].flags(), '')

    def test_push_outdated_base_text(self):
        self.test_push_two_revs()
        changes = [('adding_file', 'adding_file', 'different_content',),
                   ]
        par = revsymbol(self.repo, 'tip').rev()
        self.commitchanges(changes, parent=par)
        self.pushrevisions()
        changes = [('adding_file', 'adding_file',
                    'even_more different_content',),
                   ]
        self.commitchanges(changes, parent=par)
        try:
            self.pushrevisions()
            assert False, 'This should have aborted!'
        except hgerror.Abort, e:
            self.assertEqual(e.args[0],
                             'Outgoing changesets parent is not at subversion '
                             'HEAD\n'
                             '(pull again and rebase on a newer revision)')
            # verify that any pending transactions on the server got cleaned up
            self.assertEqual([], os.listdir(
                os.path.join(self.tmpdir, 'testrepo-1', 'db', 'transactions')))

    def test_push_encoding(self):
        self.test_push_two_revs()
        # Writing then rebasing UTF-8 filenames in a cp1252 windows console
        # used to fail because hg internal encoding was being changed during
        # the interactions with subversion, *and during the rebase*, which
        # confused the dirstate and made it believe the file was deleted.
        fn = 'pi\xc3\xa8ce/test'
        changes = [(fn, fn, 'a')]
        par = revsymbol(self.repo, 'tip').rev()
        self.commitchanges(changes, parent=par)
        self.pushrevisions()

    def test_push_emptying_changeset(self):
        r = revsymbol(self.repo, 'tip')
        changes = [
                ('alpha', None, None),
                ('beta', None, None),
                ]
        parent = revsymbol(self.repo, 'tip').rev()
        self.commitchanges(changes, parent=parent)
        self.pushrevisions()
        self.assertEqual(len(revsymbol(self.repo, 'tip').manifest()), 0)

        # Try to re-add a file after emptying the branch
        changes = [
                ('alpha', 'alpha', 'alpha'),
                ]
        self.commitchanges(changes, parent=revsymbol(self.repo, 'tip').rev())
        self.pushrevisions()
        self.assertEqual(['alpha'], list(revsymbol(self.repo, 'tip').manifest()))

    def test_push_without_pushing_children(self):
        '''
        Verify that a push of a nontip node, keeps the tip child
        on top of the pushed commit.
        '''

        oldlen = test_util.repolen(self.repo)
        oldtiphash = revsymbol(self.repo, 'default').node()

        changes = [('gamma', 'gamma', 'sometext')]
        newhash1 = self.commitchanges(changes)

        changes = [('delta', 'delta', 'sometext')]
        newhash2 = self.commitchanges(changes)

        # push only the first commit
        repo = self.repo
        hg.update(repo, newhash1)
        commands.push(repo.ui, repo)
        self.assertEqual(test_util.repolen(self.repo), oldlen + 2)

        # verify that the first commit is pushed, and the second is not
        commit2 = revsymbol(self.repo, 'tip')
        self.assertEqual(commit2.files(), ['delta', ])
        self.assertEqual(util.getsvnrev(commit2), None)
        commit1 = commit2.parents()[0]
        self.assertEqual(commit1.files(), ['gamma', ])
        prefix = 'svn:' + self.repo.svnmeta().uuid
        self.assertEqual(util.getsvnrev(commit1),
                         prefix + '/branches/the_branch@5')

    def test_push_two_that_modify_same_file(self):
        '''
        Push performs a rebase if two commits touch the same file.
        This test verifies that code path works.
        '''

        oldlen = test_util.repolen(self.repo)
        oldtiphash = revsymbol(self.repo, 'default').node()

        changes = [('gamma', 'gamma', 'sometext')]
        newhash = self.commitchanges(changes)
        changes = [('gamma', 'gamma', 'sometext\n moretext'),
                   ('delta', 'delta', 'sometext\n moretext'),
                  ]
        newhash = self.commitchanges(changes)

        repo = self.repo
        hg.update(repo, newhash)
        commands.push(repo.ui, repo)
        self.assertEqual(test_util.repolen(self.repo), oldlen + 2)

        # verify that both commits are pushed
        commit1 = revsymbol(self.repo, 'tip')
        self.assertEqual(commit1.files(), ['delta', 'gamma'])

        prefix = 'svn:' + self.repo.svnmeta().uuid
        self.assertEqual(util.getsvnrev(commit1),
                         prefix + '/branches/the_branch@6')
        commit2 = commit1.parents()[0]
        self.assertEqual(commit2.files(), ['gamma'])
        self.assertEqual(util.getsvnrev(commit2),
                         prefix + '/branches/the_branch@5')

    def test_push_in_subdir(self, commit=True):
        repo = self.repo
        old_tip = revsymbol(repo, 'tip').node()
        def file_callback(repo, memctx, path):
            if path == 'adding_file' or path == 'newdir/new_file':
                testData = 'fooFirstFile'
                if path == 'newdir/new_file':
                    testData = 'fooNewFile'
                return compathacks.makememfilectx(repo,
                                                  memctx=memctx,
                                                  path=path,
                                                  data=testData,
                                                  islink=False,
                                                  isexec=False,
                                                  copied=False)
            raise IOError(errno.EINVAL, 'Invalid operation: ' + path)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             ['adding_file'],
                             file_callback,
                             'an_author',
                             '2012-12-13 20:59:48 -0500',
                             {'branch': 'default', })
        new_hash = repo.commitctx(ctx)
        p = os.path.join(repo.root, "newdir")
        os.mkdir(p)
        ctx = context.memctx(repo,
                             (revsymbol(repo, 'default').node(), node.nullid),
                             'automated test',
                             ['newdir/new_file'],
                             file_callback,
                             'an_author',
                             '2012-12-13 20:59:48 -0500',
                             {'branch': 'default', })
        os.chdir(p)
        new_hash = repo.commitctx(ctx)
        hg.update(repo, revsymbol(repo, 'tip').node())
        self.pushrevisions()
        tip = revsymbol(self.repo, 'tip')
        self.assertNotEqual(tip.node(), old_tip)
        self.assertEqual(p, os.getcwd())
        self.assertEqual(tip['adding_file'].data(), 'fooFirstFile')
        self.assertEqual(tip['newdir/new_file'].data(), 'fooNewFile')
        self.assertEqual(tip.branch(), 'default')

    def test_update_after_push(self):
        repo = self.repo
        ui = repo.ui

        ui.setconfig('hooks',
                     'debug-hgsubversion-between-push-and-pull-for-tests',
                     lambda ui, repo, hooktype: self.add_svn_rev(
                         self.repo_path,
                         {'trunk/racey_file': 'race conditions suck'}))

        self.test_push_to_branch(push=False)
        commands.push(ui, repo)
        newctx = revsymbol(self.repo, '.')
        self.assertNotEqual(newctx.node(), revsymbol(self.repo, 'tip').node())
        self.assertEqual(newctx['adding_file'].data(), 'foo')
        self.assertEqual(newctx.branch(), 'the_branch')
