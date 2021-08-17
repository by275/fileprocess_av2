# -*- coding: utf-8 -*-
#########################################################
# python
import os
import traceback

# third-party
import requests
from flask import Blueprint, request, send_file, redirect

# sjva 공용
from framework import app, path_data, check_api, py_urllib, SystemModelSetting
from framework.logger import get_logger
from framework.util import Util
from plugin import get_model_setting, Logic, default_route, PluginUtil

# 패키지
#########################################################


class P(object):
    package_name = __name__.split('.')[0]
    logger = get_logger(package_name)
    blueprint = Blueprint(package_name, package_name, url_prefix='/%s' %  package_name, template_folder=os.path.join(os.path.dirname(__file__), 'templates'), static_folder=os.path.join(os.path.dirname(__file__), 'static'))
    menu = {
        'main' : [package_name, u'AV v2'],
        'sub' : [
            ['jav_censored', u'JavCensored'], ['jav_censored_tool', 'JavCensored Tool'], ['log', u'로그']
        ], 
        'category' : 'fileprocess',
        'sub2' : {
            'jav_censored' : [
                ['setting', u'설정'], ['list', '처리결과'],
            ],
            'jav_censored_tool' : [
                ['cd3', u'cd3 변경'],  ['purge', u'중복제거'],#['play', u'play'],
            ],
        }
    }  

    plugin_info = {
        'version' : '0.2.0.0',
        'name' : package_name,
        'category_name' : 'fileprocess',
        'icon' : '',
        'developer' : u'soju6jan',
        'description' : u'파일처리 - AV v2',
        'home' : 'https://github.com/soju6jan/%s' % package_name,
        'more' : '',
    }
    ModelSetting = get_model_setting(package_name, logger)
    logic = None
    module_list = None
    home_module = 'jav_censored'


def initialize():
    try:
        app.config['SQLALCHEMY_BINDS'][P.package_name] = 'sqlite:///%s' % (os.path.join(path_data, 'db', '{package_name}.db'.format(package_name=P.package_name)))
        PluginUtil.make_info_json(P.plugin_info, __file__)

        from .logic_jav_censored import LogicJavCensored
        from .logic_jav_censored_tool import LogicJavCensoredTool
        P.module_list = [LogicJavCensored(P), LogicJavCensoredTool(P)]
        P.logic = Logic(P)
        default_route(P)
    except Exception as e: 
        P.logger.error('Exception:%s', e)
        P.logger.error(traceback.format_exc())


initialize()

