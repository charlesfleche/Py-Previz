import contextlib
import ctypes
import logging
import os
import os.path
import Queue
import shelve
import sys
import tempfile
import time
import traceback
import urlparse
import webbrowser

import c4d

# Add locale module path
local_modules_path = os.path.join(os.path.dirname(__file__),
                                  'res',
                                  'lib',
                                  'python',
                                  'site-packages')
sys.path.insert(0, local_modules_path)

import previz

__author__ = 'Previz'
__website__ = 'https://app.previz.co'
__email__ = 'info@previz.co'
__version__ = '1.0.2'

__plugin_id__ = 1039782
__plugin_title__ = 'Previz'

DEFAULT_API_ROOT = __website__ + '/api'
DEFAULT_API_TOKEN = ''

def ids_iterator():
    for i in xrange(sys.maxint):
        yield i

ids=ids_iterator()

API_ROOT_LABEL = next(ids)
API_ROOT_EDIT  = next(ids)

API_TOKEN_LABEL  = next(ids)
API_TOKEN_EDIT   = next(ids)
API_TOKEN_BUTTON = next(ids)

TEAM_LABEL     = next(ids)
TEAM_SELECT    = next(ids)
TEAM_SPACER    = next(ids)
PROJECT_LABEL  = next(ids)
PROJECT_SELECT = next(ids)
SCENE_LABEL    = next(ids)
SCENE_SELECT   = next(ids)

PROJECT_NEW_EDIT   = next(ids)
PROJECT_NEW_BUTTON = next(ids)
SCENE_NEW_EDIT     = next(ids)
SCENE_NEW_BUTTON   = next(ids)

REFRESH_BUTTON = next(ids)
EXPORT_BUTTON  = next(ids)
PUBLISH_BUTTON = next(ids)
NEW_VERSION_BUTTON = next(ids)

DEBUG_BUTTON_SUCCESS = next(ids)
DEBUG_BUTTON_CANCEL  = next(ids)
DEBUG_BUTTON_CANCEL_CURRENT = next(ids)
DEBUG_BUTTON_RAISE   = next(ids)

MSG_PUBLISH_DONE = __plugin_id__

SETTINGS_API_ROOT  = 'api_root'
SETTINGS_API_TOKEN = 'api_token'

ERROR_MESSAGE = '''Previz error

The error has been logged to the script console.
'''

debug_canary_path = os.path.join(os.path.dirname(__file__), 'c4d_debug.txt')

debug = os.path.exists(debug_canary_path)

teams = {}
new_plugin_version = None

def key(x):
    key = 'title' if 'title' in x else 'name'
    return x[key]

def find_by_key(items, key_name, key_value):
    for item in items:
        if item[key_name] == key_value:
            return item

class Restore(object):
    def __init__(self, getter, setter, ui_id):
        self.getter = getter
        self.setter = setter
        self.ui_id  = ui_id

        self.__restore_is_needed = False

    def __call__(self, value):
        if self.__restore_is_needed:
            return
        self.__restore_is_needed = self.old_value == value

    def __enter__(self):
        self.old_value = self.getter(self.ui_id)
        return self

    def __exit__(self, type, value, traceback):
        if self.__restore_is_needed:
            self.setter(self.ui_id, self.old_value)

class Settings(object):
    def __init__(self, namespace):
        self.namespace = namespace

        self.init()

    def init(self):
        if not os.path.isdir(self.dirpath):
            os.mkdir(self.dirpath)

        with self.open('c') as shelf:
            if SETTINGS_API_ROOT not in shelf:
                shelf[SETTINGS_API_ROOT] = DEFAULT_API_ROOT
            if SETTINGS_API_TOKEN not in shelf:
                shelf[SETTINGS_API_TOKEN] = DEFAULT_API_TOKEN

    def __getitem__(self, key):
        with self.open('r') as shelf:
            return shelf[key]

    def __setitem__(self, key, value):
        with self.open('w') as shelf:
            shelf[key] = value
            shelf.sync()

    def open(self, flag='r'):
        return contextlib.closing(shelve.open(self.path, flag))

    @property
    def path(self):
        return os.path.join(self.dirpath, 'settings')

    @property
    def dirpath(self):
        return os.path.join(c4d.storage.GeGetStartupWritePath(), self.namespace)


uuids = {}
def get_id_for_uuids(uuid):
    if uuid in uuids:
        return uuids[uuid]
    id = len(uuids)+1
    uuids[uuid] = id
    return id

def get_uuid_for_id(id_to_find):
    for uuid, id in uuids.items():
        if id_to_find == id:
            return uuid


def extract(data, next_name = None):
    ret = {
        'uuid': data['id'],
        'id': get_id_for_uuids(data['id']),
        'title': data['title']
    }
    if next_name is None:
        return ret

    ret[next_name] = []
    return ret, ret[next_name]


def extract_all(teams_data):
    teams = []
    for t in teams_data:
        team, projects = extract(t, 'projects')
        teams.append(team)
        for p in t['projects']:
            project, scenes = extract(p, 'scenes')
            projects.append(project)
            for s in p['scenes']:
                scene = extract(s)
                scenes.append(scene)
    return teams

import random
import time


current_thread = None
current_thread_queue_to_main = Queue.Queue()

def get_current_thread():
    global current_thread
    return current_thread

def set_current_thread(t):
    global current_thread
    if is_task_running():
        raise RuntimeError('A thread is already running')
    current_thread = t

def register_and_start_current_thread(thread, status_text):
    set_current_thread(thread)
    c4d.StatusSetText(status_text)
    c4d.StatusSetSpin()
    thread.Start()

def unregister_current_thread():
    get_current_thread().Wait(False)
    set_current_thread(None)
    c4d.StatusClear()

def is_task_running():
    t = get_current_thread()
    return t is not None and t.IsRunning()

def terminate_current_thread():
    if not is_task_running():
        return

    log.debug('Waiting for current thread to finish')
    t = get_current_thread()
    t.End(wait=True)
    log.debug('Current thread finished')


def unpack_message(msg):
    # charlesfleche 2017-08-23
    # This is not documented in the SDK doc, but in the forums:
    # http://www.plugincafe.com/forum/forum_posts.asp?TID=10538
    # http://www.plugincafe.com/forum/forum_posts.asp?TID=7564
    # http://www.plugincafe.com/forum/forum_posts.asp?TID=10712
    def from_PyCObject(v):
        ctypes.pythonapi.PyCObject_AsVoidPtr.restype = ctypes.c_void_p
        ctypes.pythonapi.PyCObject_AsVoidPtr.argtypes = [ctypes.py_object]
        return ctypes.pythonapi.PyCObject_AsVoidPtr(v)

    return from_PyCObject(msg[c4d.BFM_CORE_PAR1]), from_PyCObject(msg[c4d.BFM_CORE_PAR2])

TASK_DONE          = next(ids)
TASK_PROGRESS      = next(ids)
TASK_PROGRESS_SPIN = next(ids)
TASK_ERROR         = next(ids)

class TestThread(c4d.threading.C4DThread):
    def __init__(self, success_timeout = None, raise_timeout = None):
        #c4d.threading.C4DThread.__init__(self)

        self.success_timeout = success_timeout
        self.raise_timeout = raise_timeout

    def done(self):
        self.send_msg(TASK_DONE)

    def progress(self, value = None):
        if value is None:
            self.send_msg(TASK_PROGRESS_SPIN)
        else:
            self.send_msg(TASK_PROGRESS, progress=value)

    def error(self):
        self.send_msg(TASK_ERROR, exc_info=sys.exc_info())

    def send_msg(self, type, **kwargs):
        msg = {
            'thread_id': c4d.threading.GeGetCurrentThreadId(),
            'type': type
        }
        msg.update(kwargs)
        current_thread_queue_to_main.put(msg)
        c4d.SpecialEventAdd(MSG_PUBLISH_DONE)

    def Main(self):
        try:
            def progress(max, cur):
                ret = cur / max
                ret *= 100
                ret = int(round(ret))
                if ret < 0:
                    return 0
                if ret > 100:
                    return 100
                return ret

            log.info('TestThread.Main: START')

            t0 = time.time()

            while True:
                st = random.random()*1.0
                time.sleep(st)

                dt = time.time() - t0

                if self.TestBreak():
                    log.info('TestThread.Main: Break')
                    self.done()
                    return

                if self.raise_timeout is not None and dt > self.raise_timeout:
                    log.info('TestThread.Main: Raise')
                    raise RuntimeError('TestThread reached raise_timeout')

                if self.success_timeout is not None:
                    if dt > self.success_timeout:
                        log.info('TestThread.Main: Success')
                        self.done()
                        return

                    p = progress(self.success_timeout, dt)
                    self.progress(p)
                else:
                    self.progress()
        except Exception:
            log.error(traceback.format_exc())
            self.error()


class PrevizDialog(c4d.gui.GeDialog):
    def __init__(self):
        self.settings = Settings(__plugin_title__)

        self.commands = {
            API_ROOT_EDIT:      self.OnAPIRootChanged,
            API_TOKEN_EDIT:     self.OnAPITokenChanged,
            API_TOKEN_BUTTON:   self.OnAPITokenButtonPressed,
            TEAM_SELECT:        self.OnTeamSelectPressed,
            PROJECT_SELECT:     self.OnProjectSelectPressed,
            SCENE_SELECT:       self.OnSceneSelectPressed,
            PROJECT_NEW_BUTTON: self.OnProjectNewButtonPressed,
            SCENE_NEW_BUTTON:   self.OnSceneNewButtonPressed,
            REFRESH_BUTTON:     self.OnRefreshButtonPressed,
            EXPORT_BUTTON:      self.OnExportButtonPressed,
            PUBLISH_BUTTON:     self.OnPublishButtonPressed,
            NEW_VERSION_BUTTON: self.OnNewVersionButtonPressed,

            DEBUG_BUTTON_SUCCESS: self.OnDebugButtonSuccessPressed,
            DEBUG_BUTTON_CANCEL:  self.OnDebugButtonCancelPressed,
            DEBUG_BUTTON_CANCEL_CURRENT: self.OnDebugButtonCancelCurrentPressed,
            DEBUG_BUTTON_RAISE:   self.OnDebugButtonRaisePressed
        }

    def OnDebugButtonSuccessPressed(self, msg):
        register_and_start_current_thread(
            TestThread(success_timeout=5.0),
            'Test success'
        )

    def OnDebugButtonCancelPressed(self, msg):
        register_and_start_current_thread(
            TestThread(),
            'Test cancel'
        )

    def OnDebugButtonCancelCurrentPressed(self, msg):
        terminate_current_thread()

    def OnDebugButtonRaisePressed(self, msg):
        register_and_start_current_thread(
            TestThread(raise_timeout=1.0),
            'Test raise'
        )

    @property
    def previz_project(self):
        api_root = self.settings[SETTINGS_API_ROOT]
        api_token = self.settings[SETTINGS_API_TOKEN]

        global teams
        team = find_by_key(teams, 'id', self.GetInt32(TEAM_SELECT))
        projects = team['projects'] if team is not None else []
        project = find_by_key(projects, 'id', self.GetInt32(PROJECT_SELECT))
        project_uuid = project['uuid'] if project is not None else None

        return previz.PrevizProject(api_root, api_token, project_uuid)

    def get_all(self):
        return extract_all(self.previz_project.get_all())

    def refresh_all(self):
        global teams
        teams = self.get_all()
        self.RefreshTeamComboBox()

        global new_plugin_version
        new_plugin_version = self.previz_project.updated_plugin('cinema4d', __version__)
        self.RefreshNewVersionButton()

    def InitValues(self):
        self.SetString(API_ROOT_EDIT, self.settings[SETTINGS_API_ROOT])
        self.SetString(API_TOKEN_EDIT, self.settings[SETTINGS_API_TOKEN])

        self.RefreshUI()

        return True

    def CreateLayout(self):
        self.SetTitle(__plugin_title__)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
                        cols=1,
                        rows=1,
                        title='Wrapper',
                        groupflags=c4d.BORDER_NONE)

        self.GroupSpace(6, 6)
        self.GroupBorderSpace(6, 6, 6, 6)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=3,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.CreateTeamLine()
        self.CreateProjectLine()
        self.CreateSceneLine()

        self.GroupEnd()

        self.AddSeparatorH(1)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT | c4d.BFV_SCALEFIT,
                        cols=1,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.CreateAPIRootLine()
        self.CreateAPITokenLine()

        self.GroupEnd()

        self.AddSeparatorH(1)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=4,
                        rows=1,
                        title='Actions',
                        groupflags=c4d.BORDER_NONE)

        self.AddButton(id=NEW_VERSION_BUTTON,
                       flags=c4d.BFH_LEFT,
                       name='') # defined in RefreshNewVersionButton

        self.AddStaticText(id=next(ids),
                           flags=c4d.BFH_SCALEFIT,
                           name='')

        self.AddButton(id=EXPORT_BUTTON,
                       flags=c4d.BFH_RIGHT,
                       name='Export to file')

        self.AddButton(id=PUBLISH_BUTTON,
                       flags=c4d.BFH_RIGHT,
                       name='Publish to Previz')

        self.GroupEnd()

        self.CreateDebugLine()

        self.GroupEnd() # Wrapper

        return True

    def CreateDebugLine(self):
        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=4,
                        rows=1,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.AddButton(id=DEBUG_BUTTON_SUCCESS,
                       flags=c4d.BFH_SCALEFIT,
                       name='Debug success')

        self.AddButton(id=DEBUG_BUTTON_CANCEL,
                       flags=c4d.BFH_SCALEFIT,
                       name='Debug cancel')

        self.AddButton(id=DEBUG_BUTTON_CANCEL_CURRENT,
                       flags=c4d.BFH_SCALEFIT,
                       name='Cancel')

        self.AddButton(id=DEBUG_BUTTON_RAISE,
                       flags=c4d.BFH_SCALEFIT,
                       name='Debug raise')

        self.GroupEnd()

    def CreateAPIRootLine(self):
        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=2,
                        rows=1,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.AddStaticText(id=API_ROOT_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='API root')

        self.AddEditText(id=API_ROOT_EDIT,
                         flags=c4d.BFH_SCALEFIT)

        self.GroupEnd()

    def CreateAPITokenLine(self):
        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=3,
                        rows=1,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.AddStaticText(id=API_TOKEN_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='API token')

        self.AddEditText(id=API_TOKEN_EDIT,
                         flags=c4d.BFH_SCALEFIT)

        self.AddButton(id=API_TOKEN_BUTTON,
                       flags=c4d.BFH_FIT,
                       name='Get a token')

        self.GroupEnd()

    def CreateTeamLine(self):
        self.AddStaticText(id=TEAM_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='Team')

        self.AddComboBox(id=TEAM_SELECT,
                         flags=c4d.BFH_SCALEFIT)

        self.AddButton(id=REFRESH_BUTTON,
                       flags=c4d.BFH_FIT,
                       name='Refresh')

    def CreateProjectLine(self):
        self.AddStaticText(id=PROJECT_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='Project')

        self.AddComboBox(id=PROJECT_SELECT,
                         flags=c4d.BFH_SCALEFIT)

        self.AddButton(id=PROJECT_NEW_BUTTON,
                       flags=c4d.BFH_FIT,
                       name='New project')

    def CreateSceneLine(self):
        self.AddStaticText(id=SCENE_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='Scene')

        self.AddComboBox(id=SCENE_SELECT,
                         flags=c4d.BFH_SCALEFIT)

        self.AddButton(id=SCENE_NEW_BUTTON,
                       flags=c4d.BFH_FIT,
                       name='New scene')

    def CoreMessage(self, id, msg):
        if id == MSG_PUBLISH_DONE:
            return self.ProcessThreadsMessages()
        return c4d.gui.GeDialog.CoreMessage(self, id, msg)

    def ProcessThreadsMessages(self):
        while not current_thread_queue_to_main.empty():
            msg = current_thread_queue_to_main.get()
            log.debug(msg)
            self.CustomThreadMessage(msg)
            current_thread_queue_to_main.task_done()
        return True

    def CustomThreadMessage(self, msg):
        type = msg['type']

        if type == TASK_DONE:
            unregister_current_thread()
            self.RefreshUI()

        if type == TASK_ERROR:
            unregister_current_thread()
            c4d.gui.MessageDialog(ERROR_MESSAGE, type=c4d.GEMB_OK)
            c4d.CallCommand(12305, 12305) # Show script console

        if type == TASK_PROGRESS:
            value = msg['progress']
            log.debug('TASK_PROGRESS: %s' % value)
            c4d.StatusSetBar(value)

        if type == TASK_PROGRESS_SPIN:
            log.debug('TASK_PROGRESS_SPIN')
            c4d.StatusSetSpin()

    def Command(self, id, msg):
        # Refresh the UI so the user has immediate feedback
        self.RefreshUI()

        # Execute command
        command = self.commands.get(id)
        if command is not None:
            command(msg)

        # If a command modify a field, no event are sent
        # Forcing UI refresh again here
        self.RefreshUI()

        return True

    def OnAPIRootChanged(self, msg):
        api_root = self.GetString(API_ROOT_EDIT)
        self.settings[SETTINGS_API_ROOT] = api_root

    def OnAPITokenChanged(self, msg):
        token = self.GetString(API_TOKEN_EDIT)
        self.settings[SETTINGS_API_TOKEN] = token

    def OnAPITokenButtonPressed(self, msg):
        api_root = self.settings[SETTINGS_API_ROOT]
        s = urlparse.urlsplit(api_root)
        url = urlparse.urlunsplit((s.scheme, s.netloc, '/account/api', '', ''))
        webbrowser.open(url)

    def OnTeamSelectPressed(self, msg):
        self.RefreshTeamComboBox()

    def OnProjectSelectPressed(self, msg):
        self.RefreshSceneComboBox()

    def OnSceneSelectPressed(self, msg):
        pass

    def OnRefreshButtonPressed(self, msg):
        self.refresh_all()

    def set_default_id_if_needed(self, id, iterable):
        if self.GetInt32(id) == -1 and len(iterable) > 0:
            v = iterable[0]['id']
            self.SetInt32(id, v)

    def RefreshTeamComboBox(self):
        with Restore(self.GetInt32, self.SetInt32, TEAM_SELECT) as touch:
            self.FreeChildren(TEAM_SELECT)

            global teams
            for team in sorted(teams, key=key):
                id   = team['id']
                name = team['title']
                self.AddChild(TEAM_SELECT, id, name)
                touch(id)

        self.set_default_id_if_needed(TEAM_SELECT, sorted(teams, key=key))

        self.LayoutChanged(TEAM_SELECT)

        self.RefreshProjectComboBox()

    def RefreshProjectComboBox(self):
        with Restore(self.GetInt32, self.SetInt32, PROJECT_SELECT) as touch:
            self.FreeChildren(PROJECT_SELECT)

            global teams
            team     = find_by_key(teams, 'id', self.GetInt32(TEAM_SELECT))
            projects = [] if team is None else team['projects']

            for project in projects:
                id   = project['id']
                title = project['title']
                self.AddChild(PROJECT_SELECT, id, title)
                touch(id)

        self.set_default_id_if_needed(PROJECT_SELECT, sorted(projects, key=key))

        self.LayoutChanged(PROJECT_SELECT)

        self.RefreshSceneComboBox()

    def RefreshSceneComboBox(self):
        with Restore(self.GetInt32, self.SetInt32, SCENE_SELECT) as touch:
            self.FreeChildren(SCENE_SELECT)

            global teams
            team     = find_by_key(teams, 'id', self.GetInt32(TEAM_SELECT))
            projects = [] if team is None else team['projects']
            project  = find_by_key(projects, 'id', self.GetInt32(PROJECT_SELECT))
            scenes   = [] if project is None else project['scenes']

            for scene in scenes:
                id = scene['id']
                name = scene['title']
                self.AddChild(SCENE_SELECT, id, name)
                touch(id)

        self.set_default_id_if_needed(SCENE_SELECT, sorted(scenes, key=key))

        self.LayoutChanged(SCENE_SELECT)

    def OnProjectNewButtonPressed(self, msg):
        project_name = c4d.gui.InputDialog('New project name')

        if len(project_name) == 0:
            return

        # New project
        global teams
        team_uuid = find_by_key(teams, 'id', self.GetInt32(TEAM_SELECT))['uuid']
        project = self.previz_project.new_project(project_name, team_uuid)
        self.refresh_all()

        # Clear project name
        # For some reason SetString doesn't send an event

        self.SetString(PROJECT_NEW_EDIT, '')
        self.RefreshProjectNewButton()

        # Select new project

        self.RefreshProjectComboBox()

        project_uuid = project['id']
        team = find_by_key(teams, 'id', self.GetInt32(TEAM_SELECT))
        projects = team['projects']
        project = find_by_key(projects, 'uuid', project_uuid)
        self.SetInt32(PROJECT_SELECT, project['id'])

    def OnSceneNewButtonPressed(self, msg):
        # New scene

        scene_name = c4d.gui.InputDialog('New scene name')

        if len(scene_name) == 0:
            return

        scene = self.previz_project.new_scene(scene_name)
        self.refresh_all()

        # Clear scene name
        # For some reason SetString doesn't send an event

        self.SetString(SCENE_NEW_EDIT, '')
        self.RefreshSceneNewButton()

        # Select new scene

        self.RefreshSceneComboBox()

        scene_uuid = scene['id']
        global teams
        team = find_by_key(teams, 'id', self.GetInt32(TEAM_SELECT))
        project = find_by_key(team['projects'], 'id', self.GetInt32(PROJECT_SELECT))
        scene = find_by_key(project['scenes'], 'uuid', scene_uuid)
        self.SetInt32(SCENE_SELECT, scene['id'])

    def OnExportButtonPressed(self, msg):
        filepath = c4d.storage.SaveDialog(
            type=c4d.FILESELECTTYPE_SCENES,
            title="Export scene to Previz JSON",
            force_suffix="json"
        )

        if filepath is None:
            return

        with open(filepath, 'w') as fp:
            previz.export(BuildPrevizScene(), fp)

    def OnPublishButtonPressed(self, msg):
        # Write JSON to disk
        scene = BuildPrevizScene()

        fp, path = tempfile.mkstemp(prefix='previz-',
                                    suffix='.json',
                                    text=True)
        fp = os.fdopen(fp, 'w')
        previz.export(scene, fp)
        fp.close()

        # Upload JSON to Previz in a thread
        api_root = self.settings[SETTINGS_API_ROOT]
        api_token = self.settings[SETTINGS_API_TOKEN]
        project_id = self.GetInt32(PROJECT_SELECT)
        scene_id = self.GetInt32(SCENE_SELECT)

        project_uuid = get_uuid_for_id(project_id)
        scene_uuid = get_uuid_for_id(scene_id)

        global publisher_thread
        publisher_thread = PublisherThread(api_root, api_token, project_uuid, scene_uuid, path)
        publisher_thread.Start()

        # Notify user of success

    def OnNewVersionButtonPressed(self, msg):
        webbrowser.open(new_plugin_version['downloadUrl'])

    def RefreshUI(self):
        self.RefreshProjectNewButton()
        self.RefreshSceneNewButton()
        self.RefreshRefreshButton()
        self.RefreshPublishButton()
        self.RefreshNewVersionButton()

    def RefreshProjectNewButton(self):
        team_id = self.GetInt32(TEAM_SELECT)
        team_id_is_valid = team_id >= 1

        project_name = self.GetString(PROJECT_NEW_EDIT)
        project_name_is_valid = len(project_name) > 0

        self.Enable(PROJECT_NEW_BUTTON, not is_task_running() and team_id_is_valid)

    def RefreshSceneNewButton(self):
        project_id = self.GetInt32(PROJECT_SELECT)
        project_id_is_valid = project_id >= 1

        scene_name = self.GetString(SCENE_NEW_EDIT)
        scene_name_is_valid = len(scene_name) > 0

        self.Enable(SCENE_NEW_BUTTON, not is_task_running() and project_id_is_valid)

    def RefreshRefreshButton(self):
        api_token = self.GetString(API_TOKEN_EDIT)
        is_api_token_valid = len(api_token) > 0
        self.Enable(REFRESH_BUTTON, not is_task_running() and is_api_token_valid)

    def RefreshPublishButton(self):
        # Token
        api_token = self.GetString(API_TOKEN_EDIT)
        is_api_token_valid = len(api_token) > 0

        # Team
        team_id = self.GetInt32(TEAM_SELECT)
        is_team_id_valid = team_id >= 1

        # Project
        project_id = self.GetInt32(PROJECT_SELECT)
        is_project_id_valid = project_id >= 1

        # Scene
        scene_id = self.GetInt32(SCENE_SELECT)
        is_scene_id_valid = scene_id >= 1

        # Publisher is running
        is_publisher_thread_running = publisher_thread is not None and publisher_thread.IsRunning()

        # Enable / Disable
        self.Enable(PUBLISH_BUTTON,
                    is_api_token_valid \
                    and is_team_id_valid \
                    and is_project_id_valid \
                    and is_scene_id_valid \
                    and not is_publisher_thread_running \
                    and not is_task_running())

    def RefreshNewVersionButton(self):
        global new_plugin_version

        text = 'Previz v%s' % __version__
        enable = False
        if new_plugin_version is not None:
            text = 'Download v%s (installed: v%s)' % (new_plugin_version['version'], __version__)
            enable = True

        self.SetString(NEW_VERSION_BUTTON, text)
        self.Enable(NEW_VERSION_BUTTON, enable)
        self.LayoutChanged(NEW_VERSION_BUTTON)


class PrevizCommandData(c4d.plugins.CommandData):
    dialog = None

    def Execute(self, doc):
        self.init_dialog_if_needed()
        return self.dialog.Open(dlgtype=c4d.DLG_TYPE_ASYNC,
                                pluginid=__plugin_id__)

    def RestoreLayout(self, sec_ref):
        self.init_dialog_if_needed()
        return self.dialog.Restore(pluginid=__plugin_id__,
                                   secret=sec_ref)

    def Message(self, type, data):
        return False

    def init_dialog_if_needed(self):
        if self.dialog is None:
            self.dialog = PrevizDialog()


################################################################################

def vertex_names(polygon):
    ret = ['a', 'b', 'c']
    if not polygon.IsTriangle():
        ret.append('d')
    return ret

def face_type(polygon, has_uvsets):
    """
    See https://github.com/mrdoob/three.js/wiki/JSON-Model-format-3
    """
    is_quad = not polygon.IsTriangle()
    return (int(is_quad) << 0) + (int(has_uvsets) << 3 )

def uvw_tags(obj):
    return list(t for t in obj.GetTags() if t.GetType() == c4d.Tuvw)

def parse_faces(obj):
    faces = []

    uvtags = uvw_tags(obj)
    has_uvsets = len(uvtags) > 0
    uvsets = [previz.UVSet(uvtag.GetName(), []) for uvtag in uvtags]

    uv_index = 0
    for polygon_index, p in enumerate(obj.GetAllPolygons()):
        three_js_face_type = face_type(p, has_uvsets)
        faces.append(three_js_face_type)

        vertex_indices = list(getattr(p, vn) for vn in vertex_names(p))
        faces.append(vertex_indices)

        uv_index_local = uv_index
        for uvtag, uvset in zip(uvtags, uvsets):
            uvdict = uvtag.GetSlow(polygon_index)
            uv_index_offset = 0
            for vn in vertex_names(p):
                uv = list((uvdict[vn].x, 1-uvdict[vn].y))
                uvset.coordinates.append(uv)
                cur_uv_index = uv_index_local + uv_index_offset
                faces.append(cur_uv_index)
                uv_index_offset += 1
        uv_index = cur_uv_index + 1

    return faces, uvsets

def get_vertices(obj):
    for v in obj.GetAllPoints():
        yield v

def parse_geometry(obj):
    vertices = ((v.x, v.y, v.z) for v in get_vertices(obj))

    uvtags = list(uvw_tags(obj))
    uvsets = [[] for i in range(len(uvtags))]

    faces, uvsets = parse_faces(obj)

    return obj.GetName() + 'Geometry', faces, vertices, uvsets

def serialize_matrix(m):
    yield m.v1.x
    yield m.v1.y
    yield m.v1.z
    yield 0

    yield m.v2.x
    yield m.v2.y
    yield m.v2.z
    yield 0

    yield m.v3.x
    yield m.v3.y
    yield m.v3.z
    yield 0

    yield m.off.x
    yield m.off.y
    yield m.off.z
    yield 1

AXIS_CONVERSION = c4d.utils.MatrixScale(c4d.Vector(1, 1, -1))

def convert_matrix(matrix):
    return serialize_matrix(AXIS_CONVERSION * matrix)

def parse_mesh(obj):
    name = obj.GetName()
    world_matrix = convert_matrix(obj.GetMg())
    geometry_name, faces, vertices, uvsets = parse_geometry(obj)

    return previz.Mesh(name,
                       geometry_name,
                       world_matrix,
                       faces,
                       vertices,
                       uvsets)

def iterate(obj):
    down = obj.GetDown()
    if down is not None:
        for o in iterate(down):
            yield o

    yield obj

    next = obj.GetNext()
    if next is not None:
        for o in iterate(next):
            yield o

def traverse(doc):
    objs = doc.GetObjects()
    if len(objs) == 0:
        return []
    return iterate(objs[0])

def exportable_objects(doc):
    return (o for o in traverse(doc) if isinstance(o, c4d.PolygonObject))

def build_objects(doc):
    for o in exportable_objects(doc):
        yield parse_mesh(o)

def BuildPrevizScene():
    log.info('BuildPrevizScene')

    doc = c4d.documents.GetActiveDocument()
    doc = doc.Polygonize()

    return previz.Scene('Cinema4D-Previz',
                        os.path.basename(doc.GetDocumentPath()),
                        None,
                        build_objects(doc))


class PublisherThread(c4d.threading.C4DThread):
    def __init__(self, api_root, api_token, project_uuid, scene_uuid, path):
        self.api_root = api_root
        self.api_token = api_token
        self.project_uuid = project_uuid
        self.scene_uuid = scene_uuid
        self.path = path

    def Main(self):
        p = previz.PrevizProject(self.api_root,
                                 self.api_token,
                                 self.project_uuid)
        scene = p.scene(self.scene_uuid, include=[])
        json_url = scene['jsonUrl']
        with open(self.path, 'rb') as fp:
            log.info('Start upload: %s' % json_url)
            p.update_scene(json_url, fp)
            log.info('End upload: %s' % json_url)
        c4d.SpecialEventAdd(MSG_PUBLISH_DONE)


publisher_thread = None



log = None
handlers = []

def register_logger():
    global log
    global handlers

    lvl = logging.DEBUG if debug else logging.INFO
    log = logging.getLogger(__plugin_title__)
    log.setLevel(lvl)

    formatter = logging.Formatter('%(name)s:%(levelname)s %(message)s')

    sh = logging.StreamHandler()
    sh.setLevel(lvl)
    sh.setFormatter(formatter)

    log.addHandler(sh)
    handlers.append(sh)


def unregister_logger():
    global log
    global handlers

    if log is None:
        return

    for handler in handlers:
        log.removeHandler(handler)


def make_terminate_thread_callback(text):
    def func(msg):
        log.debug(text)
        terminate_current_thread()
        unregister_logger()
    return func


plugin_messages = {
    c4d.C4DPL_ENDACTIVITY:         make_terminate_thread_callback('C4DPL_ENDACTIVITY'),
    c4d.C4DPL_RELOADPYTHONPLUGINS: make_terminate_thread_callback('C4DPL_RELOADPYTHONPLUGINS'),
    c4d.C4DPL_ENDPROGRAM:          make_terminate_thread_callback('C4DPL_ENDPROGRAM')
}


def PluginMessage(id, data):
    cb = plugin_messages.get(id, lambda msg: True)
    return cb(data)


if __name__ == '__main__':
    register_logger()
    if debug:
        log.debug('Activated debug mode has this file exists:')
        log.debug(debug_canary_path)
    c4d.plugins.RegisterCommandPlugin(id=__plugin_id__,
                                  str='Py-Previz',
                                  help='Py - Previz',
                                  info=0,
                                  dat=PrevizCommandData(),
                                  icon=None)
