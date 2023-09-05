import os
import re
import shutil
import time
import traceback
from datetime import datetime
from pathlib import Path

# third-party
from flask import jsonify, render_template, request
from sqlalchemy import desc, or_

# sjva 공용
from framework import app, celery, db, scheduler
from framework.util import Util
from plugin import LogicModuleBase

# 패키지
from .plugin import P
from .tool import ToolExpandFileProcess

logger = P.logger
package_name = P.package_name
ModelSetting = P.ModelSetting


class LogicJavCensored(LogicModuleBase):
    db_default = {
        "jav_censored_db_version": "1",
        # auto
        "jav_censored_auto_start": "False",
        "jav_censored_interval": "60",
        # basic
        "jav_censored_download_path": "",
        "jav_censored_temp_path": "",
        "jav_censored_remove_path": "",
        "jav_censored_min_size": "300",
        "jav_censored_max_age": "0",
        "jav_censored_filename_not_allowed_list": "",
        # filename
        "jav_censored_include_original_filename": "True",
        "jav_censored_include_original_filename_option": "0",
        "jav_censored_filename_test": "",
        # folders
        "jav_censored_folder_format": "{label}/{code}",
        "jav_censored_use_meta": "0",
        # folders w/o meta
        "jav_censored_target_path": "",
        # folders w/ meta
        "jav_censored_make_nfo": "False",
        "jav_censored_meta_dvd_path": "",
        "jav_censored_meta_dvd_vr_path": "",
        "jav_censored_meta_dvd_use_dmm_only": "False",
        "jav_censored_folder_format_actor": "",
        "jav_censored_meta_dvd_labels_exclude": "",
        "jav_censored_meta_dvd_labels_include": "",
        "jav_censored_meta_ama_path": "",
        "jav_censored_meta_no_path": "",
        "jav_censored_meta_no_retry_every": "0",
        "jav_censored_meta_no_last_retry": "1970-01-01T00:00:00",
        # etc
        "jav_censored_last_list_option": "",
    }

    def __init__(self, PM):
        super().__init__(PM, "setting")
        self.name = "jav_censored"

    def process_menu(self, sub, req):
        arg = ModelSetting.to_dict()
        arg["sub"] = self.name
        if sub == "setting":
            job_id = f"{package_name}_{self.name}"
            arg["scheduler"] = str(scheduler.is_include(job_id))
            arg["is_running"] = str(scheduler.is_running(job_id))
        try:
            return render_template(f"{package_name}_{self.name}_{sub}.html", arg=arg)
        except Exception:
            logger.exception("메뉴 처리 중 예외:")
            return render_template("sample.html", title=f"{package_name} - {sub}")

    def process_ajax(self, sub, req):
        try:
            if sub == "web_list":
                return jsonify(ModelJavcensoredItem.web_list(request))
            if sub == "db_remove":
                return jsonify(ModelJavcensoredItem.delete_by_id(req.form["id"]))
            if sub == "filename_test":
                filename = req.form["filename"]
                ModelSetting.set("jav_censored_filename_test", filename)
                newfilename = ToolExpandFileProcess.change_filename_censored(filename)
                newfilename = LogicJavCensored.check_newfilename(filename, newfilename, None)
                return jsonify({"ret": "success", "data": newfilename})
            raise NotImplementedError(f"Unknown Ajax sub={sub}")
        except Exception as e:
            logger.exception("AJAX 요청 처리 중 예외:")
            return jsonify({"ret": "exception", "log": str(e)})

    def scheduler_function(self):
        if app.config["config"]["use_celery"]:
            result = LogicJavCensored.task.apply_async()
            result.get()
        else:
            LogicJavCensored.task()

    def reset_db(self):
        db.session.query(ModelJavcensoredItem).delete()
        db.session.commit()
        return True

    #########################################################

    @staticmethod
    def __is_duplicate(src, dst):
        """이름 뿐만 아니라 용량도 확인하여 중복 결정"""
        if dst.exists():
            return True
        bytes_in_dst = [f.stat().st_size for f in dst.parent.iterdir() if f.is_file()]
        return src.stat().st_size in bytes_in_dst

    @staticmethod
    def __get_target_with_meta_dvd(search_name):
        label = search_name.split(" ")[0]
        meta_dvd_labels_exclude = map(str.strip, ModelSetting.get_list("jav_censored_meta_dvd_labels_exclude", ","))
        if label in map(str.lower, meta_dvd_labels_exclude):
            logger.info("'정식발매 영상 제외 레이블'에 포함: %s", label)
            return None, None

        target_root = ModelSetting.get("jav_censored_meta_dvd_path").strip() or None
        if target_root is None:
            raise NotADirectoryError("'정식발매 영상 매칭시 이동 경로'가 지정되지 않음")
        target_root = Path(target_root)
        if not target_root.is_dir():
            raise NotADirectoryError("'정식발매 영상 매칭시 이동 경로'가 존재하지 않음")

        from metadata import Logic as MetadataLogic

        if ModelSetting.get_bool("jav_censored_meta_dvd_use_dmm_only"):
            logger.info("정식발매 영상 판단에 DMM+MGS만 사용")
            data = (
                MetadataLogic.get_module("jav_censored").search2(search_name, "dmm", manual=False)
                or MetadataLogic.get_module("jav_censored").search2(search_name, "mgsdvd", manual=False)
                or []
            )
        else:
            data = MetadataLogic.get_module("jav_censored").search(search_name, manual=False)

        if len(data) > 0 and data[0]["score"] > 95:
            meta_info = MetadataLogic.get_module("jav_censored").info(data[0]["code"])
            if meta_info is not None:
                folders = LogicJavCensored.process_folder_format("dvd", meta_info)
                if any(x in (meta_info["genre"] or []) for x in ["고품질VR", "VR전용"]) or any(
                    x in (meta_info["title"] or "") for x in ["[VR]", "[ VR ]", "【VR】"]
                ):
                    vr_path = ModelSetting.get("jav_censored_meta_dvd_vr_path").strip()
                    if vr_path != "":
                        vr_path = Path(vr_path)
                        if vr_path.is_dir():
                            target_root = vr_path
                        else:
                            logger.warning("'정식발매 VR영상 이동 경로'가 존재하지 않음")
                logger.info("정식발매 영상 매칭 성공")
                return target_root.joinpath(*folders), meta_info

        meta_dvd_labels_include = map(str.strip, ModelSetting.get_list("jav_censored_meta_dvd_labels_include", ","))
        if label in map(str.lower, meta_dvd_labels_include):
            folders = LogicJavCensored.process_folder_format("normal", search_name)
            logger.info("정식발매 영상 매칭 실패하였지만 '정식발매 영상 포함 레이블'에 포함: %s", label)
            return target_root.joinpath(*folders), None
        return None, None

    @staticmethod
    def __get_target_with_meta(search_name):
        target_dir, meta_info = LogicJavCensored.__get_target_with_meta_dvd(search_name)
        if target_dir is not None:
            return target_dir, "dvd", meta_info

        target_root = ModelSetting.get("jav_censored_meta_ama_path").strip() or None
        if target_root is None:
            raise NotADirectoryError("'그 외 매칭시 이동 경로'가 지정되지 않음")
        target_root = Path(target_root)
        if not target_root.is_dir():
            raise NotADirectoryError("'그 외 매칭시 이동 경로'가 존재하지 않음")

        from metadata import Logic as MetadataLogic

        data = MetadataLogic.get_module("jav_censored_ama").search(search_name, manual=False)
        if data is not None and len(data) > 0 and data[0]["score"] > 95:
            meta_info = MetadataLogic.get_module("jav_censored_ama").info(data[0]["code"])
            if meta_info is not None:
                move_type = "ama"
                folders = LogicJavCensored.process_folder_format(move_type, meta_info)
                logger.info("아마추어 메타 검색 성공")
                return Path(target_root).joinpath(*folders), move_type, meta_info

        move_type = "no_meta"
        # NO META인 경우 폴더 구조를 만들지 않음
        target_root = ModelSetting.get("jav_censored_meta_no_path").strip()
        if not (target_root and Path(target_root).exists()):
            target_root = Path(ModelSetting.get("jav_censored_temp_path").strip()).joinpath("[NO META]")
        logger.info("메타 없음으로 최종 판별")
        return Path(target_root), move_type, None

    @staticmethod
    def __move(src, trg):
        if trg.exists():
            trg = trg.with_name(f"[{int(time.time())}] {trg.name}")
        shutil.move(src, trg)
        logger.debug("Moved: %s -> %s", src.name, trg)

    @staticmethod
    def __task(file):
        newfilename = ToolExpandFileProcess.change_filename_censored(file.name)
        newfilename = LogicJavCensored.check_newfilename(file.name, newfilename, str(file))

        # 검색용 키워드
        search_name = ToolExpandFileProcess.change_filename_censored(newfilename)
        search_name = search_name.split(".")[0]
        search_name = os.path.splitext(search_name)[0].replace("-", " ")
        search_name = re.sub(r"\s*\[.*?\]", "", search_name).strip()
        match = re.search(r"(?P<cd>cd\d{1,2})$", search_name)
        if match:
            search_name = search_name.replace(match.group("cd"), "")
        logger.debug("search_name=%s", search_name)

        #
        # target_dir를 결정하라!
        #
        target_dir, move_type, meta_info = None, None, None
        if ModelSetting.get("jav_censored_use_meta") == "0":
            move_type = "normal"
            target = LogicJavCensored.get_path_list("jav_censored_target_path")
            folders = LogicJavCensored.process_folder_format(move_type, search_name)
            # 첫번째 자식폴더만 타겟에서 찾는다.
            for tmp in target:
                tmp_dir = Path(tmp).joinpath(folders[0])
                if tmp_dir.exists():
                    target_dir = tmp_dir
                    break
            # 없으면 첫번째 타겟으로
            if target_dir is None and target:
                target_dir = Path(target[0]).joinpath(*folders)
        else:
            # 메타 처리
            try:
                target_dir, move_type, meta_info = LogicJavCensored.__get_target_with_meta(search_name)
            except Exception:
                logger.exception("메타를 이용한 타겟 폴더 결정 중 예외:")
        logger.debug("target_dir: %s", target_dir)

        # 2021-04-30
        try:
            match = re.compile(r"\d+\-?c(\.|\].)|\(").search(file.stem.lower())
            if match:
                for cd in ["1", "2", "4"]:
                    cd_name = f'{search_name.replace(" ", "-")}cd{cd}{file.suffix}'
                    cd_file = Path(target_dir).joinpath(cd_name)
                    if cd_file.exists():
                        newfilename = f'{search_name.replace(" ", "-")}cd3{file.suffix}'
                        break
        except Exception as e:
            logger.debug("Exception:%s", e)
            logger.debug(traceback.format_exc())

        #
        # 실제 이동
        #
        entity = ModelJavcensoredItem(str(file.parent), file.name)
        if move_type is None or target_dir is None:
            logger.warning("타겟 폴더를 결정할 수 없음")
            return entity.set_move_type(None)

        if not target_dir.exists():
            target_dir.mkdir(parents=True)

        if move_type == "no_meta":
            newfile = target_dir.joinpath(file.name)  # 원본 파일명 그대로
            if file == newfile:
                # 처리한 폴더를 다시 처리했을 때 중복으로 삭제되지 않아야 함
                return entity.set_move_type(None)
            LogicJavCensored.__move(file, newfile)
            return entity.set_target(newfile).set_move_type(move_type)

        newfile = target_dir.joinpath(newfilename)

        if LogicJavCensored.__is_duplicate(file, newfile):
            logger.debug("동일 파일(크기, 이름 기준)이 존재함: %s -> %s", file, newfile.parent)
            remove_path = ModelSetting.get("jav_censored_remove_path").strip()
            if remove_path == "":
                file.unlink()
                logger.debug("Deleted: %s", file)
            else:
                dup = Path(remove_path).joinpath(file.name)
                LogicJavCensored.__move(file, dup)
            move_type += "_already_exist"

        if file.exists():
            shutil.move(file, newfile)
            if ModelSetting.get_bool("jav_censored_make_nfo") and meta_info is not None:
                from lib_metadata.util_nfo import UtilNfo

                nfopath = target_dir.joinpath("movie.nfo")
                if not nfopath.exists():
                    UtilNfo.make_nfo_movie(meta_info, output="save", savepath=str(nfopath))
            try:
                from .secret import Secret

                Secret.autoscan(str(newfile))
            except ImportError:
                pass
        return entity.set_target(newfile).set_move_type(move_type)

    @staticmethod
    def __add_meta_no_path():
        meta_no_path = ModelSetting.get("jav_censored_meta_no_path").strip()
        if not meta_no_path:
            meta_no_path = Path(ModelSetting.get("jav_censored_temp_path").strip()).joinpath("[NO META]")
            if not meta_no_path.is_dir():
                return []
            meta_no_path = str(meta_no_path)
        meta_no_retry_every = ModelSetting.get_int("jav_censored_meta_no_retry_every")
        if meta_no_retry_every <= 0:
            return []
        meta_no_last_retry = datetime.fromisoformat(ModelSetting.get("jav_censored_meta_no_last_retry"))
        if (datetime.now() - meta_no_last_retry).days < meta_no_retry_every:
            return []
        ModelSetting.set("jav_censored_meta_no_last_retry", datetime.now().isoformat())
        return [meta_no_path]

    @staticmethod
    @celery.task
    def task():
        no_censored_path = ModelSetting.get("jav_censored_temp_path").strip()
        if not no_censored_path:
            logger.warning("'처리 실패시 이동 폴더'가 지정되지 않음. 작업 중단!")
            return
        no_censored_path = Path(no_censored_path)
        if not no_censored_path.is_dir():
            logger.warning("'처리 실패시 이동 폴더'가 존재하지 않음. 작업 중단: %s", no_censored_path)
            return

        src_list = LogicJavCensored.get_path_list("jav_censored_download_path")
        src_list += LogicJavCensored.__add_meta_no_path()
        if not src_list:
            logger.warning("'다운로드 폴더'가 지정되지 않음. 작업 중단!")
            return

        min_size = ModelSetting.get_int("jav_censored_min_size")
        max_age = ModelSetting.get_int("jav_censored_max_age")
        disallowed_keys = ModelSetting.get_list("jav_censored_filename_not_allowed_list", "|")

        #
        # 전처리
        #
        files = []
        for src in src_list:
            ToolExpandFileProcess.preprocess_cleanup(src, min_size=min_size, max_age=max_age)
            _f = ToolExpandFileProcess.preprocess_listdir(
                src, no_censored_path, min_size=min_size, disallowed_keys=disallowed_keys
            )
            files += _f or []
        logger.info("처리할 파일 %d개: %s", len(files), list(map(str, files)))

        #
        # 본처리
        #
        for idx, file in enumerate(files):
            logger.debug("[%03d/%03d] %s", idx + 1, len(files), file.name)
            try:
                entity = LogicJavCensored.__task(file)
            except Exception:
                logger.exception("개별 파일 처리 중 예외: %s", file)
            else:
                if entity.move_type is not None:
                    entity.save()

    @staticmethod
    def process_folder_format(meta_type, meta_info):
        folders = None
        folder_format = ModelSetting.get("jav_censored_folder_format")
        if meta_type in ["normal", "no_meta"]:
            return folder_format.format(
                code=meta_info.replace(" ", "-").upper(),
                label=meta_info.split(" ")[0].upper(),
                label_1=meta_info.split(" ")[0].upper()[0],
            ).split("/")

        studio = meta_info.get("studio", None) or "NO_STUDIO"
        code = meta_info["originaltitle"]
        label = meta_info["originaltitle"].split("-")[0]
        match = re.compile(r"\d{2}id", re.I).search(label)
        if match:
            label = "ID"
        label_1 = label[0]

        if meta_type == "dvd":
            folder_format_actor = ModelSetting.get("jav_censored_folder_format_actor")
            if (
                folder_format_actor != ""
                and meta_info["actor"] is not None
                and len(meta_info["actor"]) == 1
                and meta_info["actor"][0]["originalname"] != meta_info["actor"][0]["name"]
                and meta_info["actor"][0]["name"] != ""
            ):
                folders = folder_format_actor.format(
                    code=code,
                    label=label,
                    actor=meta_info["actor"][0]["name"],
                    studio=studio,
                    label_1=label_1,
                ).split("/")

        if folders is None:
            return folder_format.format(code=code, label=label, studio=studio, label_1=label_1).split("/")
        return folders

    @staticmethod
    def check_newfilename(filename, newfilename, file_path):
        # 이미 파일처리를 한거라면..
        # newfilename 과 filename 이 [] 제외하고 같다면 처리한파일로 보자
        # 그런 파일은 다시 원본파일명 옵션을 적용하지 않아야한다.
        # logger.debug(filename)
        # logger.debug(newfilename)
        # adn-091-uncenrosed.mp4
        # 같이 시작하더라도 [] 가 없다면... 변경
        # [] 없거나, 시작이 다르면..  완벽히 일치 하지 않으면

        # 2021-04-21 ??????
        if filename == newfilename and filename.find("[") == -1 and filename.find("]") == -1:
            newfilename = LogicJavCensored.change_filename_censored_by_save_original(filename, newfilename, file_path)
        elif filename != newfilename and (
            (filename.find("[") == -1 or filename.find("]") == -1)
            or not os.path.splitext(filename)[0].startswith(os.path.splitext(newfilename)[0])
        ):
            newfilename = LogicJavCensored.change_filename_censored_by_save_original(filename, newfilename, file_path)
        else:
            # 이미 한번 파일처리를 한것으로 가정하여 변경하지 않는다.
            newfilename = filename
            # 기존에 cd1 [..].mp4 는 []를 제거한다
            match = re.search(r"cd\d(?P<remove>\s\[.*?\])", newfilename)
            if match:
                newfilename = newfilename.replace(match.group("remove"), "")

        logger.debug("%s => %s", filename, newfilename)
        return newfilename

    @staticmethod
    def change_filename_censored_by_save_original(original_filename, new_filename, original_filepath):
        """원본파일명 보존 옵션에 의해 파일명을 변경한다."""
        try:
            if not ModelSetting.get_bool("jav_censored_include_original_filename"):
                return new_filename

            new_name, new_ext = os.path.splitext(new_filename)
            part = None
            match = re.search(r"(?P<part>cd\d+)$", new_name)
            if match:
                # cd1 앞에가 같아야함.
                return new_filename
                # part = match.group("part")
                # new_name = new_name.replace(part, "")

            ori_name, _ = os.path.splitext(original_filename)
            # 2019-07-30
            ori_name = ori_name.replace("[", "(").replace("]", ")").strip()
            if part is not None:
                # 안씀
                return f"{new_name} [{ori_name}] {part}{new_ext}"

            option = ModelSetting.get("jav_censored_include_original_filename_option")
            if option == "0" or original_filepath is None:
                return f"{new_name} [{ori_name}]{new_ext}"
            if option == "1":
                str_size = os.stat(original_filepath).st_size
                return f"{new_name} [{ori_name}({str_size})]{new_ext}"
            if option == "2":
                str_size = Util.sizeof_fmt(os.stat(original_filepath).st_size, suffix="B")
                return f"{new_name} [{ori_name}({str_size})]{new_ext}"
            if option == "3":
                str_size = os.stat(original_filepath).st_size
                return f"{new_name} [{str_size}]{new_ext}"
            return f"{new_name} [{ori_name}]{new_ext}"
        except Exception as exception:
            logger.debug("Exception:%s", exception)
            logger.debug(traceback.format_exc())

    @staticmethod
    def get_path_list(key):
        tmps = map(str.strip, ModelSetting.get(key).splitlines())
        ret = []
        for t in tmps:
            if not t or t.startswith("#"):
                continue
            if t.endswith("*"):
                dirname = os.path.dirname(t)
                listdirs = os.listdir(dirname)
                for l in listdirs:
                    ret.append(os.path.join(dirname, l))
            else:
                ret.append(t)
        return ret


class ModelJavcensoredItem(db.Model):
    __tablename__ = f"{package_name}_jav_censored_item"
    __table_args__ = {"mysql_collate": "utf8_general_ci"}
    __bind_key__ = package_name

    id = db.Column(db.Integer, primary_key=True)
    created_time = db.Column(db.DateTime)
    reserved = db.Column(db.JSON)

    is_file = db.Column(db.Boolean)
    source_dir = db.Column(db.String)
    source_filename = db.Column(db.String)
    source_path = db.Column(db.String)
    move_type = db.Column(db.String)  # -1, 0:정상, 1:타입불일치, 2:중복삭제
    target_dir = db.Column(db.String)
    target_filename = db.Column(db.String)
    target_path = db.Column(db.String)
    log = db.Column(db.String)

    meta_result = db.Column(db.String)
    poster = db.Column(db.String)

    def __init__(self, source_dir, source_filename):
        self.created_time = datetime.now()
        self.is_file = True
        self.source_dir = source_dir
        self.source_filename = source_filename
        self.move_type = None

    def __repr__(self):
        return repr(self.as_dict())

    def as_dict(self):
        ret = {x.name: getattr(self, x.name) for x in self.__table__.columns}
        ret["created_time"] = self.created_time.strftime("%m-%d %H:%M:%S")
        return ret

    def save(self):
        db.session.add(self)
        db.session.commit()

    def set_move_type(self, move_type):
        self.move_type = move_type
        return self

    def set_target(self, trg):
        trg = Path(trg)
        self.target_dir = str(trg.parent)
        self.target_filename = trg.name
        return self

    @classmethod
    def get_by_id(cls, _id):
        return db.session.query(cls).filter_by(id=_id).first()

    @classmethod
    def web_list(cls, req):
        ret = {}
        page_size = 30
        try:
            page = int(req.form.get("page", "1"))
            search = req.form.get("search_word", "")
            option = req.form["option"]
            order = req.form.get("order", "desc")

            query = cls.make_query(search=search, option=option, order=order)
            count = query.count()
            query = query.limit(page_size).offset((page - 1) * page_size)
            # logger.debug("cls count:%s", count)
            lists = query.all()
            ret["list"] = [item.as_dict() for item in lists]
            ret["paging"] = Util.get_paging_info(count, page, page_size)
            ModelSetting.set("jav_censored_last_list_option", f"{option}|{order}|{search}|{page}")
        except Exception:
            logger.exception("게시판 리스트 구성 중 예외:")
        return ret

    @classmethod
    def make_query(cls, search="", option="all", order="desc"):
        query = db.session.query(cls)
        if search is not None and search != "":
            if search.find("|") != -1:
                conditions = []
                for tt in search.split("|"):
                    if tt != "":
                        conditions.append(cls.source_filename.like("%" + tt.strip() + "%"))
                query = query.filter(or_(*conditions))
            elif search.find(",") != -1:
                for tt in search.split(","):
                    if tt != "":
                        query = query.filter(cls.source_filename.like("%" + tt.strip() + "%"))
            else:
                query = query.filter(
                    or_(
                        cls.source_filename.like("%" + search + "%"),
                        cls.target_filename.like("%" + search + "%"),
                    )
                )

        # if av_type != 'all':
        #    query = query.filter(cls.av_type == av_type)

        if option != "all":
            query = query.filter(cls.move_type.like("%" + option + "%"))

        if order == "desc":
            query = query.order_by(desc(cls.id))
        else:
            query = query.order_by(cls.id)

        return query
