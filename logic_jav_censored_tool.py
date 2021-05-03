# -*- coding: utf-8 -*-
#########################################################
# python
import os, sys, traceback, re, json, threading, time, shutil, fnmatch, glob
from datetime import datetime
# third-party
import requests
# third-party
from flask import request, render_template, jsonify, redirect
from sqlalchemy import or_, and_, func, not_, desc

# sjva 공용
from framework import db, scheduler, path_data, socketio, SystemModelSetting, app, celery, Util
from framework.common.plugin import LogicModuleBase, default_route_socketio
from tool_expand import ToolExpandFileProcess
from tool_base import ToolShutil

# 패키지
from .plugin import P
logger = P.logger
package_name = P.package_name
ModelSetting = P.ModelSetting

#########################################################

class LogicJavCensoredTool(LogicModuleBase):
    db_default = {
        'jav_censored_tool_db_version' : '1',
        'jav_censored_tool_cd3_path' : '',
        'jav_censored_tool_cd3_auto_change' : 'False',
        'jav_censored_tool_cd3_stop_flag' : 'False',
        'jav_censored_tool_play_filepath' : '',
    }


    def __init__(self, P):
        super(LogicJavCensoredTool, self).__init__(P, 'cd3')
        self.name = 'jav_censored_tool'
        self.cd3_data = []
        self.is_working = False
        default_route_socketio(P, self)

    def process_menu(self, sub, req):
        arg = P.ModelSetting.to_dict()
        arg['sub'] = self.name
        if sub == 'cd3':
            arg['data'] = self.cd3_data
        return render_template('{package_name}_{module_name}_{sub}.html'.format(package_name=P.package_name, module_name=self.name, sub=sub), arg=arg)

    def process_ajax(self, sub, req):
        try:
            if sub == 'send_command':
                name = req.form['name']
                command = req.form['command']
                if name == 'cd3':
                    if command == 'data':
                        return jsonify({'data':self.cd3_data, 'status':self.is_working})
                    elif command == 'stop':
                        if self.is_working:
                            ModelSetting.set('jav_censored_tool_cd3_stop_flag', 'True')
                            return jsonify({'ret':'success', 'msg':u'작업을 중지합니다.'})
                        else:
                            return jsonify({'ret':'warning', 'msg':u'작업중이 아닙니다.'})

                    elif command == 'start':
                        arg1 = req.form['arg1']
                        ModelSetting.set('jav_censored_tool_cd3_path', arg1)
                        if req.form['arg2'] == 'true':
                            ModelSetting.set('jav_censored_tool_cd3_auto_change', 'True')
                        else:
                            ModelSetting.set('jav_censored_tool_cd3_auto_change', 'False')
                        ModelSetting.set('jav_censored_tool_cd3_stop_flag', 'False')
                        def func():
                            #notify = {'type':'success', 'msg' : u'작업을 시작합니다.'}
                            #socketio.emit("notify", notify, namespace='/framework', broadcast=True)
                            self.cd3_task_start()
                            
                        thread = threading.Thread(target=func, args=())
                        thread.daemon = True  
                        thread.start()
                        self.cd3_data = []
                        self.socketio_callback('data', {'data':self.cd3_data, 'status':self.is_working})
                        return jsonify({'ret':'success', 'msg':u'작업을 시작하였습니다.'})
                    elif command == 'change':
                        arg1 = req.form['arg1'].split('_')
                        arg2 = req.form['arg2']
                        code_data = self.cd3_data[int(arg1[0])]
                        fix_data = code_data['fix_files'][int(arg1[1])]
                        logger.debug(code_data)
                        logger.debug(arg2)
                        if fix_data['filename'] == arg2:
                            return jsonify({'ret':'warning', 'msg':u'동일 파일명'})
                        fix_data['ret'] = 'change_' + str(ToolShutil.move(os.path.join(code_data['base'], fix_data['filename']), os.path.join(code_data['base'], arg2), run_in_celery=False))
                        fix_data['newfilename'] = arg2
                        return jsonify({'ret':'success', 'msg':u'파일명을 변경합니다.<br>' + arg2, 'list_data':{'data':self.cd3_data, 'status':self.is_working}})
                    elif command == 'delete':
                        arg1 = req.form['arg1'].split('_')
                        arg2 = req.form['arg2']
                        code_data = self.cd3_data[int(arg1[0])]
                        fix_data = code_data['fix_files'][int(arg1[1])]
                        fix_data['ret'] = 'delete_' + str(ToolShutil.remove(os.path.join(code_data['base'], fix_data['filename'])))
                        return jsonify({'ret':'success', 'msg':u'파일을 삭제합니다.<br>' + arg2, 'list_data':{'data':self.cd3_data, 'status':self.is_working}})
                elif name == 'play':
                    if command == 'save_filename':
                        ModelSetting.set('jav_censored_tool_play_filepath', req.form['arg1'])
                        return jsonify({})

        except Exception as e: 
            P.logger.error('Exception:%s', e)
            P.logger.error(traceback.format_exc())
            return jsonify({'ret':'exception', 'log':str(e)})

    #########################################################

    def cd3_task_start(self):
        try:
            self.is_working = True
            if app.config['config']['use_celery']:
                result = LogicJavCensoredTool.cd3_task.apply_async()
                try:
                    ret = result.get(on_message=self.cd3_receive_from_task, propagate=True)
                except:
                    logger.debug('CELERY on_message not process.. only get() start')
                    ret = result.get()
            else:
                LogicJavCensoredTool.cd3_task()
            self.is_working = False
            self.socketio_callback('data', {'data':self.cd3_data, 'status':self.is_working})
        except Exception as e: 
            P.logger.error('Exception:%s', e)
            P.logger.error(traceback.format_exc())

    #@staticmethod
    def cd3_receive_from_task(self, arg):
        logger.debug('receive_from_task : %s' % arg)
        try:
            if arg['status'] == 'PROGRESS':
                result = arg['result']
                #logger.debug('receive_from_task : %s' % arg)
                self.cd3_data.append(result)
                self.socketio_callback('data', {'data':self.cd3_data, 'status':self.is_working})
                #if 'filename' in result:
                #    Logic.send_to_listener(result['filename'])
        except Exception as exception: 
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())
            
    
    
    @staticmethod
    @celery.task(bind=True)
    def cd3_task(self):
        try:
            cd3_path = ModelSetting.get('jav_censored_tool_cd3_path')
            ret = []
            pattern = '*cd1*'
            for base, dirs, files in os.walk(cd3_path):
                files = fnmatch.filter(files, pattern)
                
                for cd1_file in files:
                    if ModelSetting.get_bool('jav_censored_tool_cd3_stop_flag'):
                        return ret
                    code = cd1_file.split('cd')[0]
                    code_files = glob.glob('%s/%s*' % (base, code))
                    tmp = [] 
                    for code_file in code_files:
                        entity = {'filename':os.path.split(code_file)[-1]}
                        tmp.append(entity)
                    code_files = tmp

                    fix_files = []
                    count = 0
                    auto_target = None
                    if len(code_files) == 1:
                        #change_files.append({'filename' : tmp, 'newfilename':'%%s' % (code, os.path.splitext(tmp)[-1])})
                        data = {'base':base, 'code':code, 'files':code_files}
                        fix_files.append({'ret':'', 'filename' : code_files[0]['filename'], 'newfilename':'%s%s' % (code, os.path.splitext(code_files[0]['filename'])[-1])})
                        pass
                    else:
                        for code_file in code_files:
                            if not code_file['filename'].startswith('%scd' % code):
                                auto_target = {'ret':'', 'filename' : code_file['filename'], 'newfilename':'%scd3%s' % (code, os.path.splitext(code_file['filename'])[-1])}
                                fix_files.append(auto_target)
                                count += 1
                            else:
                                fix_files.append({'ret':'', 'filename' : code_file['filename'], 'newfilename':code_file['filename']})

                    if ModelSetting.get_bool('jav_censored_tool_cd3_auto_change'):
                        if count == 1 and auto_target is not None:
                            tmp = auto_target['filename'].lower()
                            if tmp.find('-c.') != -1 or tmp.find('-c].') != -1 or tmp.find('-c(') != -1:
                                auto_target['ret'] = 'change_' + str(ToolShutil.move(os.path.join(base, auto_target['filename']), os.path.join(base, auto_target['newfilename']), run_in_celery=True))
                                  
                    if count > 0 :
                        data = {'base':base, 'code':code, 'files':code_files, 'fix_files':fix_files}
                        for code_file in code_files:
                            try:
                                import ffmpeg
                                code_file['info'] = ffmpeg.get_video_info(os.path.join(base, code_file['filename']))
                                code_file['info']['format']['duration_str'] = code_file['info']['format']['duration'].split('.')[0]
                                tmp = int(code_file['info']['format']['duration_str'])
                                code_file['info']['format']['duration_str'] = '%02d:%02d:%02d' % ((tmp/3600), (tmp % 3600) / 60, tmp % 60)
                            except Exception as exception: 
                                logger.error('Exception:%s', exception)
                                logger.error(traceback.format_exc())
                        if app.config['config']['use_celery']:
                            self.update_state(state='PROGRESS', meta=data)
                        else:
                            P.logic.get_module('jav_censored_tool').cd3_receive_from_task(data)
                        ret.append(data)
                    

            return ret
        except Exception as exception: 
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())
