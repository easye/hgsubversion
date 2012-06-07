#!/usr/bin/env python
# -*- coding: utf-8 -*-
import os
import re
import subprocess
import sys
import time
if not hasattr(sys, 'version_info') or sys.version_info < (2, 4, 0, 'final'):
    raise SystemExit("Mercurial requires python 2.4 or later.")

try:
    from distutils.command.build_py import build_py_2to3 as build_py
except ImportError:
    from distutils.command.build_py import build_py
try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

def runcmd(cmd, env):
    shell = os.name == 'nt'
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, shell=shell,
                         stderr=subprocess.PIPE, env=env)
    out, err = p.communicate()
    # If root is executing setup.py, but the repository is owned by
    # another user (as in "sudo python setup.py install") we will get
    # trust warnings since the .hg/hgrc file is untrusted. That is
    # fine, we don't want to load it anyway.
    err = [e for e in err.splitlines()
           if not e.startswith('Not trusting file')]
    if err:
        return ''
    return out


version = ''

if os.path.isdir('.hg'):
    # Execute hg out of this directory with a custom environment which
    # includes the pure Python modules in mercurial/pure. We also take
    # care to not use any hgrc files and do no localization.
    env = {'HGRCPATH': '',
           'LANGUAGE': 'C'}
    for copyenv in ('LD_LIBRARY_PATH', 'PYTHONPATH', 'PATH'):
        if copyenv in os.environ:
            env[copyenv] = os.environ[copyenv]
    if 'SystemRoot' in os.environ:
        # Copy SystemRoot into the custom environment for Python 2.6
        # under Windows. Otherwise, the subprocess will fail with
        # error 0xc0150004. See: http://bugs.python.org/issue3440
        env['SystemRoot'] = os.environ['SystemRoot']
    cmd = ['hg', 'id', '-i', '-t']
    l = runcmd(cmd, env).split()
    while len(l) > 1 and l[-1][0].isalpha(): # remove non-numbered tags
        l.pop()
    if len(l) > 1: # tag found
        version = l[-1]
        if l[0].endswith('+'): # propagate the dirty status to the tag
            version += '+'
    elif len(l) == 1: # no tag found
        cmd = ['hg', 'parents', '--template',
               '{latesttag}+{latesttagdistance}-']
        version = runcmd(cmd, env) + l[0]
    if not version:
        version = runcmd(['hg', 'parents', '--template' '{node|short}\n'],
                         env)
        if version:
            version = version.split()[0]
    if version.endswith('+'):
        version += time.strftime('%Y%m%d')
elif os.path.exists('.hg_archival.txt'):
    kw = dict([t.strip() for t in l.split(':', 1)]
              for l in open('.hg_archival.txt'))
    if 'tag' in kw:
        version = kw['tag']
    elif 'latesttag' in kw:
        version = '%(latesttag)s+%(latesttagdistance)s-%(node).12s' % kw
    else:
        version = kw.get('node', '')[:12]

verfile = os.path.join("hgsubversion", "__version__.py")
if version:
    f = open(verfile, "w")
    f.write('# this file is autogenerated by setup.py\n')
    f.write('version = "%s"\n' % version)
    f.close()

if os.path.exists(verfile):
    # scrape the version out with a regex because setuptools
    # needlessly swaps out file() for some non-object thing
    # and breaks importing hgsubversion entirely
    mat = re.findall('.*"(.*)"', open(verfile).read())
    version = mat[0]
if not version:
    version = 'unknown'

requires = []
try:
    import mercurial
except ImportError:
    requires.append('mercurial')

# If the Subversion SWIG bindings aren't present, require Subvertpy
try:
    from hgsubversion.svnwrap import svn_swig_wrapper
except ImportError:
    requires.append('subvertpy>=0.7.4')

setup(
    name='hgsubversion',
    version=version,
    url='http://bitbucket.org/durin42/hgsubversion',
    license='GNU GPL',
    author='Augie Fackler, others',
    author_email='durin42@gmail.com',
    description=('hgsubversion is a Mercurial extension for working with '
                   'Subversion repositories.'),
    long_description=open(os.path.join(os.path.dirname(__file__),
                                         'README')).read(),
    keywords='mercurial',
    packages=('hgsubversion', 'hgsubversion.hooks', 'hgsubversion.svnwrap'),
    package_data={ 'hgsubversion': ['help/subversion.rst'] },
    platforms='any',
    install_requires=requires,
    classifiers=[
        'License :: OSI Approved :: GNU General Public License (GPL)',
        'Intended Audience :: Developers',
        'Topic :: Software Development :: Version Control',
        'Development Status :: 4 - Beta',
        'Programming Language :: Python',
        'Operating System :: OS Independent',
    ],
    cmdclass={'build_py': build_py},
)
