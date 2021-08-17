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
from plugin import LogicModuleBase, default_route_socketio
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
        
        'jav_censored_tool_purge_path' : '',
        'jav_censored_tool_purge_stop_flag' : 'False',
    }


    def __init__(self, P):
        super(LogicJavCensoredTool, self).__init__(P, 'cd3')
        self.name = 'jav_censored_tool'

        self.data = {
            'cd3' : {
                'task' : LogicJavCensoredTool.cd3_task,
                'data' : [],
                'is_working' : False
            },
            'purge' : {
                'task' : LogicJavCensoredTool.purge_task,
                'data' : [],
                'is_working' : False
            }
        }
        default_route_socketio(P, self)
        #app.config['config']['use_celery'] = False

    def process_menu(self, sub, req):
        arg = P.ModelSetting.to_dict()
        arg['sub'] = self.name
        return render_template('{package_name}_{module_name}_{sub}.html'.format(package_name=P.package_name, module_name=self.name, sub=sub), arg=arg)

    def process_ajax(self, sub, req):
        try:
            if sub == 'send_command':
                tool_name = req.form['name']
                command = req.form['command']
                if tool_name in ['cd3', 'purge']:
                    if command == 'data':
                        self.refresh_data(tool_name)
                        return jsonify({})
                    elif command == 'stop':
                        if self.data[tool_name]['is_working']:
                            ModelSetting.set('jav_censored_tool_%s_stop_flag' % tool_name, 'True')
                            ret = {'ret':'success', 'msg':u'작업을 중지합니다.'}
                        else:
                            ret = {'ret':'warning', 'msg':u'작업중이 아닙니다.'}
                        return jsonify(ret)


                if tool_name == 'cd3':
                    ret = self.cd3_process_send_command(command, req.form['arg1'], req.form['arg2'])
                    return jsonify(ret)
                elif tool_name == 'purge':
                    ret = self.purge_process_send_command(command, req.form['arg1'], req.form['arg2'])
                    return jsonify(ret)
                elif tool_name == 'play':
                    if command == 'save_filename':
                        ModelSetting.set('jav_censored_tool_play_filepath', req.form['arg1'])
                        return jsonify({})
                
                    
        except Exception as e: 
            P.logger.error('Exception:%s', e)
            P.logger.error(traceback.format_exc())
            return jsonify({'ret':'exception', 'log':str(e)})

    #########################################################

    def refresh_data(self, tool_name):
        self.socketio_callback('%s_data' % tool_name, {'data':self.data[tool_name]['data'], 'status':self.data[tool_name]['is_working']})

    def task_start(self, tool_name):
        try:
            self.data[tool_name]['is_working'] = True
            if app.config['config']['use_celery']:
                result = self.data[tool_name]['task'].apply_async()
                try:
                    ret = result.get(on_message=self.receive_from_task, propagate=True)
                except:
                    logger.debug('CELERY on_message not process.. only get() start')
                    ret = result.get()
            else:
                self.data[tool_name]['task'](None)
            self.data[tool_name]['is_working'] = False
            self.refresh_data(tool_name)
        except Exception as e: 
            P.logger.error('Exception:%s', e)
            P.logger.error(traceback.format_exc())

    def receive_from_task(self, arg, celery=True):
        try:
            result = None
            if celery:
                if arg['status'] == 'PROGRESS':
                    result = arg['result']
            else:
                result = arg
            if result is not None:
                tool_name = result['tool_name']
                self.data[tool_name]['data'].append(result)
                self.refresh_data(tool_name)
        except Exception as exception: 
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())

    #########################################################

    def cd3_process_send_command(self, command, arg1, arg2):
        if command == 'start':
            ModelSetting.set('jav_censored_tool_cd3_path', arg1)
            if arg2 == 'true':
                ModelSetting.set('jav_censored_tool_cd3_auto_change', 'True')
            else:
                ModelSetting.set('jav_censored_tool_cd3_auto_change', 'False')
            ModelSetting.set('jav_censored_tool_cd3_stop_flag', 'False')
            def func():
                self.task_start('cd3')
                
            thread = threading.Thread(target=func, args=())
            thread.daemon = True  
            thread.start()
            self.data['cd3']['data'] = []
            self.refresh_data('cd3')
            ret = {'ret':'success', 'msg':u'작업을 시작하였습니다.'}
        elif command == 'change':
            arg1 = arg1.split('_')
            code_data = self.data['cd3']['data'][int(arg1[0])]
            fix_data = code_data['fix_files'][int(arg1[1])]
            if fix_data['filename'] == arg2:
                return jsonify({'ret':'warning', 'msg':u'동일 파일명'})
            fix_data['ret'] = 'change_' + str(ToolShutil.move(os.path.join(code_data['base'], fix_data['filename']), os.path.join(code_data['base'], arg2), run_in_celery=False))
            fix_data['newfilename'] = arg2
            ret = {'ret':'success', 'msg':u'파일명을 변경합니다.<br>' + arg2}
        elif command == 'delete':
            arg1 = arg1.split('_')
            code_data = self.data['cd3']['data'][int(arg1[0])]
            fix_data = code_data['fix_files'][int(arg1[1])]
            fix_data['ret'] = 'delete_' + str(ToolShutil.remove(os.path.join(code_data['base'], fix_data['filename'])))
            ret = {'ret':'success', 'msg':u'파일을 삭제합니다.<br>' + arg2}
        self.refresh_data('cd3')
        return ret



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
                        data = {'tool_name':'cd3', 'base':base, 'code':code, 'files':code_files, 'fix_files':fix_files}
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
                            P.logic.get_module('jav_censored_tool').receive_from_task(data, celery=False)
                        ret.append(data)
            return ret
        except Exception as exception: 
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())


    #########################################################

    
    def purge_process_send_command(self, command, arg1, arg2):
        if command == 'start':
            ModelSetting.set('jav_censored_tool_purge_path', arg1)
            ModelSetting.set('jav_censored_tool_purge_stop_flag', 'False')
            def func():
                self.task_start('purge')
            thread = threading.Thread(target=func, args=())
            thread.daemon = True  
            thread.start()
            self.data['purge']['data'] = []
            ret = {'ret':'success', 'msg':u'작업을 시작하였습니다.'}
        elif command in  ['change', 'delete']:
            arg1 = arg1.split('_')
            code_data = self.data['purge']['data'][int(arg1[0])]
            file_data = code_data['files'][int(arg1[1])]
            ret = {'ret':'success', 'msg':u''}

            if command == 'change':
                if file_data['filename'] == arg2:
                    ret['ret'] = 'warning'
                    ret['msg'] = u'동일 파일명'
                    return ret
                if ToolShutil.move(os.path.join(code_data['base'], file_data['filename']), os.path.join(code_data['base'], arg2), run_in_celery=False):
                    file_data['log'] += u'%s => %s 파일명 변경\n' % (file_data['filename'], arg2)
                    file_data['filename'] = arg2
                    ret['msg'] = u'파일명 변경'
                else:
                    file_data['log'] += u'%s => %s 파일명 변경 실패\n' % (file_data['filename'], arg2)
                    ret['ret'] = 'warning'
                    ret['msg'] = u'파일명 변경 실패'
            elif command == 'delete':
                if ToolShutil.remove(os.path.join(code_data['base'], file_data['filename'])):
                    file_data['log'] += u'%s 파일 삭제\n' % (file_data['filename'])
                    file_data['filename'] = ''
                    ret['msg'] = u'파일 삭제'
                else:
                    file_data['log'] += u'%s 파일 삭제 실패\n' % (file_data['filename'])
                    ret['ret'] = 'warning'
                    ret['msg'] = u'파일 삭제 실패'
        self.refresh_data('purge')
        return ret



    @staticmethod
    @celery.task(bind=True)
    def purge_task(self):
        try:
            work_path = ModelSetting.get('jav_censored_tool_purge_path')
            ret = []
            pattern = '*'
            code_list = []
            regex = re.compile(r'(?P<code>\w+-\d+)')
            cd_regex = re.compile('cd\d\.')

            for base, dirs, files in os.walk(work_path):
                files = fnmatch.filter(files, pattern)
                
                for child_file in files:
                    if ModelSetting.get_bool('jav_censored_tool_purge_stop_flag'):
                        return ret
                    match = regex.match(child_file)
                    if not match:
                        continue
                    code = match.group('code')
                    if code in code_list:
                        continue
                    code_files = glob.glob('%s/%s*' % (base, code))
                    tmp = [] 
                    if len(code_files) == 1:
                        continue
                    is_all_cd = True
                    for code_file in code_files:
                        entity = {'filename':os.path.split(code_file)[-1], 'log':''}
                        if not cd_regex.search(entity['filename']):
                            is_all_cd = False
                        tmp.append(entity)
                    if is_all_cd:
                        continue
                    code_files = tmp
                                  
                    data = {'tool_name':'purge', 'base':base, 'code':code, 'files':code_files}
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
                        P.logic.get_module('jav_censored_tool').receive_from_task(data, celery=False)
                    code_list.append(code)
                    ret.append(data)
            return ret
        except Exception as exception: 
            logger.error('Exception:%s', exception)
            logger.error(traceback.format_exc())


## ffmpeg -ss 00:00:30 -i HND-980-C.mp4 -frames:v 1  -f image2 a2.jpg
# 