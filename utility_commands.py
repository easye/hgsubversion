from mercurial import cmdutil
from mercurial import node
from hgext import rebase

import util
import hg_delta_editor

@util.register_subcommand('url')
def print_wc_url(ui, repo, hg_repo_path, **opts):
    hge = hg_delta_editor.HgChangeReceiver(hg_repo_path,
                                           ui_=ui)
    ui.status(hge.url, '\n')


@util.register_subcommand('parent')
def print_parent_revision(ui, repo, hg_repo_path, **opts):
    """Prints the hg hash and svn revision info for the nearest svn parent of
    the current revision"""
    hge = hg_delta_editor.HgChangeReceiver(hg_repo_path,
                                           ui_=ui)
    svn_commit_hashes = dict(zip(hge.revmap.itervalues(),
                                 hge.revmap.iterkeys()))
    ha = repo.parents()[0]
    o_r = outgoing_revisions(ui, repo, hge, svn_commit_hashes)
    if o_r:
        ha = repo[o_r[-1]].parents()[0]
    if ha.node() != node.nullid:
        r, br = svn_commit_hashes[ha.node()]
        ui.status('Working copy parent revision is %s: r%s on %s\n' %
                  (ha, r, br or 'trunk'))
    else:
        ui.status('Working copy seems to have no parent svn revision.\n')
    return 0


@util.register_subcommand('rebase')
def rebase_commits(ui, repo, hg_repo_path, **opts):
    """Rebases the current uncommitted revisions onto the top of the branch.
    """
    hge = hg_delta_editor.HgChangeReceiver(hg_repo_path,
                                           ui_=ui)
    svn_commit_hashes = dict(zip(hge.revmap.itervalues(),
                                 hge.revmap.iterkeys()))
    o_r = outgoing_revisions(ui, repo, hge, svn_commit_hashes)
    if not o_r:
        ui.status('Nothing to rebase!\n')
        return 0
    if len(repo.parents()[0].children()):
        ui.status('Refusing to rebase non-head commit like a coward\n')
        return 0
    parent_rev = repo[o_r[-1]].parents()[0]
    target_rev = parent_rev
    p_n = parent_rev.node()
    exhausted_choices = False
    while target_rev.children() and not exhausted_choices:
        for c in target_rev.children():
            exhausted_choices = True
            n = c.node()
            if (n in svn_commit_hashes and
                svn_commit_hashes[n][1] == svn_commit_hashes[p_n][1]):
                target_rev = c
                exhausted_choices = False
                break
    if parent_rev == target_rev:
        ui.status('Already up to date!\n')
        return 0
    # TODO this is really hacky, there must be a more direct way
    return rebase.rebase(ui, repo, dest=node.hex(target_rev.node()),
                         base=node.hex(repo.parents()[0].node()))


@util.register_subcommand('outgoing')
def show_outgoing_to_svn(ui, repo, hg_repo_path, **opts):
    """Commit the current revision and any required parents back to svn.
    """
    hge = hg_delta_editor.HgChangeReceiver(hg_repo_path,
                                           ui_=ui)
    svn_commit_hashes = dict(zip(hge.revmap.itervalues(),
                                 hge.revmap.iterkeys()))
    o_r = outgoing_revisions(ui, repo, hge, svn_commit_hashes)
    if not (o_r and len(o_r)):
        ui.status('No outgoing changes found.\n')
        return 0
    displayer = cmdutil.show_changeset(ui, repo, opts, buffered=False)
    for rev in reversed(o_r):
        displayer.show(changenode=rev)


def outgoing_revisions(ui, repo, hg_editor, reverse_map):
    """Given a repo and an hg_editor, determines outgoing revisions for the
    current working copy state.
    """
    outgoing_rev_hashes = []
    working_rev = repo.parents()
    assert len(working_rev) == 1
    working_rev = working_rev[0]
    if working_rev.node() in reverse_map:
        return
    while (not working_rev.node() in reverse_map
           and working_rev.node() != node.nullid):
        outgoing_rev_hashes.append(working_rev.node())
        working_rev = working_rev.parents()
        assert len(working_rev) == 1
        working_rev = working_rev[0]
    if working_rev.node() != node.nullid:
        return outgoing_rev_hashes
