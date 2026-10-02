"""Qt appearance host: bounded painter banner, independent of runtime timers."""
from __future__ import annotations
import json
from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QAction, QColor, QLinearGradient, QPainter, QPen
from PySide6.QtWidgets import QWidget, QVBoxLayout
from shiboken6 import isValid
from pyharness.ui.appearance import normalize

PALETTES = {
    'light':('#f5f8fc','#ffffff','#101d37','#dce5f0','#1672ff'),
    'dark':('#142034','#1e2b41','#e1e9f6','#425570','#75b3ff'),
    'tech-blue-particle':('#071322','#10263c','#e7f3ff','#305873','#66caff'),
}


class ParticleBanner(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self.setFixedHeight(44)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)
        self.setAccessibleName('静态或粒子装饰背景')
        self.phase=0.0; self.config=normalize({})
        self.timer=QTimer(self); self.timer.setInterval(33)
        self.timer.timeout.connect(self.advance)
        self.visibility_changed = None

    def eventFilter(self, obj, event):
        if self.visibility_changed and event.type() in (QEvent.Show, QEvent.Hide, QEvent.WindowStateChange):
            self.visibility_changed()
        return False

    def advance(self):
        self.phase=(self.phase+self.config['speed']*.002)%1
        self.update()

    def paintEvent(self, event):
        painter=QPainter(self); painter.setRenderHint(QPainter.Antialiasing)
        bg,panel,text,line,accent=PALETTES[self.config['themeId']]
        gradient=QLinearGradient(0,0,self.width(),self.height())
        gradient.setColorAt(0,QColor(panel));gradient.setColorAt(1,QColor(bg))
        painter.fillRect(self.rect(),gradient)
        painter.setPen(QColor(text));painter.drawText(14,27,'PyHarness · 受治理的 Agent Runtime')
        if self.config['themeId']=='tech-blue-particle' and self.config['animationMode']=='normal':
            color=QColor(accent);color.setAlphaF(self.config['opacity'])
            painter.setPen(QPen(color,2))
            for n in range(self.config['count']):
                x=320+((n*.618+self.phase)%1)*max(1,self.width()-325)
                y=7+((n*.381+self.phase)%1)*30
                painter.drawEllipse(int(x),int(y),2,2)
        painter.end()


class NativeAppearance:
    def __init__(self, window, path=None):
        self.window=window;self.dead=False
        self.settings=QSettings(str(path),QSettings.IniFormat) if path else None
        try: self.config=normalize(json.loads(self.settings.value('appearance','{}')) if self.settings else {})
        except (ValueError,TypeError): self.config=normalize({})
        self.original_style=window.styleSheet();self.original_name=window.objectName()
        window.setObjectName('PyHarnessNativeTheme')
        content=window.takeCentralWidget()
        self.wrapper=QWidget(window);layout=QVBoxLayout(self.wrapper);layout.setContentsMargins(0,0,0,0)
        self.banner=ParticleBanner(self.wrapper);layout.addWidget(self.banner)
        if content:layout.addWidget(content,1)
        window.setCentralWidget(self.wrapper)
        self.menu=window.menuBar().addMenu('外观')
        for label,theme in [('原主题 · 浅色','light'),('原主题 · 深色','dark'),('科技蓝 · 粒子','tech-blue-particle')]:
            action=QAction(label,window);action.triggered.connect(lambda checked=False,t=theme:self.update({'themeId':t}));self.menu.addAction(action)
        self.menu.addSeparator()
        for label,mode in [('开启动效','normal'),('低动效（静态）','low'),('关闭动效','off')]:
            action=QAction(label,window);action.triggered.connect(lambda checked=False,m=mode:self.update({'animationMode':m}));self.menu.addAction(action)
        self.banner.visibility_changed = self.sync_timer
        window.installEventFilter(self.banner)
        window.destroyed.connect(self.destroy)
        self.apply()

    def sync_timer(self):
        if self.dead:
            return
        if not isValid(self.window):
            self.destroy()
            return
        active=(not self.dead and self.window.isVisible() and not self.window.isMinimized()
                and self.config['themeId']=='tech-blue-particle' and self.config['animationMode']=='normal')
        if active:self.banner.timer.start()
        else:self.banner.timer.stop()

    def apply(self):
        bg,panel,text,line,accent=PALETTES[self.config['themeId']]
        self.window.setStyleSheet(f'''QWidget#PyHarnessNativeTheme {{background:{bg};color:{text};}}
QWidget#PyHarnessNativeTheme QWidget {{background:{panel};color:{text};}}
QWidget#PyHarnessNativeTheme QLineEdit, QWidget#PyHarnessNativeTheme QTextEdit,
QWidget#PyHarnessNativeTheme QPlainTextEdit, QWidget#PyHarnessNativeTheme QTextBrowser {{background:{bg};color:{text};selection-background-color:{accent};}}
QWidget#PyHarnessNativeTheme QPushButton {{border:1px solid {line};padding:5px;border-radius:4px;}}
QWidget#PyHarnessNativeTheme QWidget:focus {{border:1px solid {accent};}}
QWidget#PyHarnessNativeTheme QWidget:disabled {{color:#7a8797;}}''')
        self.banner.config=dict(self.config);self.banner.update();self.sync_timer()

    def update(self,value):
        if self.dead:return
        self.config=normalize({**self.config,**value});self.apply()
        if self.settings:
            self.settings.setValue('appearance',json.dumps(self.config));self.settings.sync()

    def destroy(self):
        if self.dead:return
        self.dead=True
        self.banner.visibility_changed = None
        if isValid(self.banner.timer):self.banner.timer.stop()
        # Qt may destroy a window without delivering closeEvent. Its Python
        # wrapper can already be invalid while Hide/destruction is dispatched.
        if not isValid(self.window):return
        self.window.removeEventFilter(self.banner)
        self.window.setStyleSheet(self.original_style);self.window.setObjectName(self.original_name)
        self.window.menuBar().removeAction(self.menu.menuAction());self.menu.deleteLater()
        self.banner.hide()
