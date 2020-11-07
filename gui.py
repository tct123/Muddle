#
# This document *and this document only* uses the Qt naming convention instead
# of PEP because the PyQt5 bindings do not have pythonic names
#
import os
import platform
import subprocess
import sys
import json
import enum
import html
import logging
import tempfile

from PyQt5 import uic
from PyQt5.QtGui import QFont
from PyQt5.Qt import QStyle
from PyQt5.QtCore import Qt, QThread, pyqtSlot, pyqtSignal, QObject
from PyQt5.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QTreeWidgetItemIterator,
    QGridLayout,
    QHBoxLayout,
    QPushButton,
    QToolButton,
    QProgressBar,
    QTabWidget,
    QPlainTextEdit
)

import moodle

log = logging.getLogger("muddle.gui")

class MoodleItem(QTreeWidgetItem):
    class Type(enum.IntEnum):
        ROOT       = 0
        # root
        COURSE     = 1
        # sections
        SECTION    = 2
        # modules
        MODULE     = 3
        ## specific module types
        FORUM      = 4
        RESOURCE   = 5
        FOLDER     = 6
        ATTENDANCE = 7
        LABEL      = 8
        QUIZ       = 9
        # contents
        CONTENT    = 10
        ## specific content types
        FILE       = 11
        URL        = 12

    class Metadata:
        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

    def __init__(self, parent, nodetype, leaves=[], **kwargs):
        super().__init__(parent)
        self.metadata = MoodleItem.Metadata(type = nodetype, **kwargs)
        self.setupQt()

    def setupQt(self):
        font = QFont("Monospace")
        font.setStyleHint(QFont.Monospace)
        self.setFont(0, font)

        icons = {
            MoodleItem.Type.COURSE   : QStyle.SP_DriveNetIcon,
            MoodleItem.Type.FOLDER   : QStyle.SP_DirIcon,
            MoodleItem.Type.RESOURCE : QStyle.SP_DirLinkIcon,
            MoodleItem.Type.FILE     : QStyle.SP_FileIcon,
            MoodleItem.Type.URL      : QStyle.SP_FileLinkIcon,
        }

        if icons.get(self.metadata.type):
            self.setIcon(0, QApplication.style().standardIcon(icons[self.metadata.type]))

        flags = self.flags()
        if self.metadata.type in [ MoodleItem.Type.FILE, MoodleItem.Type.FOLDER, MoodleItem.Type.RESOURCE ]:
            self.setFlags(flags | Qt.ItemIsUserCheckable | Qt.ItemIsAutoTristate)
            self.setCheckState(0, Qt.Unchecked)
        else:
            self.setFlags(flags & ~Qt.ItemIsUserCheckable)

        self.setChildIndicatorPolicy(QTreeWidgetItem.DontShowIndicatorWhenChildless)
        self.setText(0, html.unescape(self.metadata.title))

class MoodleFetcher(QThread):
    loadedItem = pyqtSignal(MoodleItem.Type, object)

    def __init__(self, parent, instance_url, token):
        super().__init__()

        self.api = moodle.RestApi(instance_url, token)
        self.apihelper = moodle.ApiHelper(self.api)

    def run(self):
        for course in self.getCourses():
            self.loadedItem.emit(MoodleItem.Type.COURSE, course)
            for section in self.getSections(course):
                self.loadedItem.emit(MoodleItem.Type.SECTION, section)
                for module in self.getModules(section):
                    self.loadedItem.emit(MoodleItem.Type.MODULE, module)
                    for content in self.getContent(module):
                        self.loadedItem.emit(MoodleItem.Type.CONTENT, content)

    def getCourses(self):
        coursesReq = self.api.core_enrol_get_users_courses(userid = self.apihelper.get_userid())
        if not coursesReq:
            return []

        return coursesReq.json()

    def getSections(self, course):
        if not "id" in course:
            log.error("cannot get sections from invalid course (no id)")
            log.debug(course)
            return []

        sectionsReq = self.api.core_course_get_contents(courseid = str(course["id"]))
        if not sectionsReq:
            return []

        sections = sectionsReq.json()
        return sections

    def getModules(self, section):
        if "modules" in section:
            return section["modules"]
        else:
            return []

    def getContent(self, module):
        if "contents" in module:
            return module["contents"]
        else:
            return []


class MoodleTreeWidget(QTreeWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.itemDoubleClicked.connect(self.onItemDoubleClicked, Qt.QueuedConnection)

        self.lastInsertedItem = None
        self.worker = None

    @pyqtSlot(str, str)
    def refresh(self, instance_url, token):
        if not self.worker or not self.worker.isFinished():
            self.worker = MoodleFetcher(self, instance_url, token)
            self.worker.loadedItem.connect(self.onWorkerLoadedItem)
            self.worker.finished.connect(self.onWorkerDone)
            self.worker.start()
        else:
            log.debug("A worker is already running, not refreshing")

    @pyqtSlot(QTreeWidgetItem, int)
    def onItemDoubleClicked(self, item, col):
        log.debug(f"double clicked on item with type {str(item.metadata.type)}")
        if item.metadata.type == MoodleItem.Type.FILE:
            log.debug(f"started download from {item.metadata.url}")
            filepath = tempfile.gettempdir()+"/"+item.metadata.title
            self.worker.apihelper.get_file(item.metadata.url, filepath)

            if platform.system() == 'Darwin':       # macOS
                subprocess.Popen(('open', filepath))
            elif platform.system() == 'Windows':    # Windows
                os.startfile(filepath)
            else:                                   # linux variants
                subprocess.Popen(('xdg-open', filepath))

    @pyqtSlot(MoodleItem.Type, object)
    def onWorkerLoadedItem(self, type, item):
        # Assume that the items arrive in order
        moodleItem = None
        parent = None

        if type == MoodleItem.Type.COURSE:
            moodleItem = MoodleItem(parent = parent, nodetype = type, id = item["id"], title = item["shortname"])
            self.addTopLevelItem(moodleItem)
        else:
            parent = self.lastInsertedItem
            while type <= parent.metadata.type and parent.parent():
                parent = parent.parent()

        if type == MoodleItem.Type.SECTION:
            moodleItem = MoodleItem(parent = parent, nodetype = type, id = item["id"], title = item["name"])
        elif type == MoodleItem.Type.MODULE:
            moduleType = {
                "folder"     : MoodleItem.Type.FOLDER,
                "resource"   : MoodleItem.Type.RESOURCE,
                "forum"      : MoodleItem.Type.FORUM,
                "attendance" : MoodleItem.Type.ATTENDANCE,
                "label"      : MoodleItem.Type.LABEL,
                "quiz"       : MoodleItem.Type.QUIZ,
            }

            moodleItem = MoodleItem(parent = parent, nodetype = moduleType.get(item["modname"]) or type, id = item["id"], title = item["name"])
        elif type == MoodleItem.Type.CONTENT:
            contentType = {
                "url" : MoodleItem.Type.URL,
                "file" : MoodleItem.Type.FILE,
            }
            moodleItem = MoodleItem(parent = parent, nodetype = contentType.get(item["type"]) or type, title = item["filename"], url = item["fileurl"])

        if not moodleItem:
            log.error(f"Could not load item of type {type}")
            return

        self.lastInsertedItem = moodleItem

    @pyqtSlot()
    def onWorkerDone(self):
        log.debug("worker done")
        self.sortByColumn(0, Qt.AscendingOrder)
        self.setSortingEnabled(True)


class QLogHandler(QObject, logging.Handler):
    newLogMessage = pyqtSignal(str)

    def emit(self, record):
        msg = self.format(record)
        self.newLogMessage.emit(msg)

    def write(self, m):
        pass


class MuddleWindow(QMainWindow):
    def __init__(self, instance_url, token):
        super(MuddleWindow, self).__init__()
        uic.loadUi("muddle.ui", self)
        self.setCentralWidget(self.findChild(QTabWidget, "Muddle"))

        # setup logging
        self.loghandler = QLogHandler(self)
        self.loghandler.setFormatter(logging.Formatter("%(name)s - %(levelname)s - %(message)s"))
        self.loghandler.newLogMessage.connect(self.onNewLogMessage)
        logging.getLogger("muddle").addHandler(self.loghandler)

        refreshBtn = self.findChild(QToolButton, "refreshBtn")
        moodleTreeWidget = self.findChild(MoodleTreeWidget, "moodleTree")
        refreshBtn.clicked.connect(lambda b: moodleTreeWidget.refresh(instance_url, token))

        self.show()

    @pyqtSlot(str)
    def onNewLogMessage(self, msg):
        self.findChild(QPlainTextEdit, "logsTab").appendPlainText(msg)


def start(instance_url, token):
    app = QApplication(sys.argv)
    ex = MuddleWindow(instance_url, token)
    sys.exit(app.exec_())
