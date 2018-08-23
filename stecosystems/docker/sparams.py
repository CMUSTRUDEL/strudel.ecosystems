#!/usr/bin/env python
# -*- coding: utf-8 -*-

import setuptools
from distutils import core
import json
import types


def fooinput(*args, **kwargs):
    return ''


# suppress occasional inputs some developers put into setup.py
input = raw_input = fooinput


def mock_setup(*args, **params):
    for i, arg in enumerate(args):
        params[i] = arg
    if 'ext_modules' in params:
        params['ext_modules'] = [ext.name for ext in params['ext_modules']]
    if 'distclass' in params:
        params['distclass'] = str(params['distclass'])
    if 'cmdclass' in params:
        params['cmdclass'] = str(params['cmdclass'])
    for key in params:
        if isinstance(params[key], types.GeneratorType):
            params[key] = list(params[key])
    open('output.json', 'wb').write(json.dumps(params).encode('utf8'))


setuptools.setup = mock_setup
core.setup = mock_setup
