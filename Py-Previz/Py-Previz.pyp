import contextlib
import os
import os.path
import shelve
import sys
import tempfile
import time
import webbrowser

import c4d
from c4d import documents, gui, plugins, storage, threading

# Add locale module path
local_modules_path = os.path.join(os.path.dirname(__file__),
                                  'res',
                                  'lib',
                                  'python',
                                  'site-packages')
sys.path.insert(0, local_modules_path)

import previz

__author__ = 'Charles Flèche'
__website__ = 'https://dandelion-burdock.beanstalkapp.com/'
__email__ = 'charles.fleche@gmail.com'
__version__ = "0.0.9"

__plugin_id__ = 938453
__plugin_title__ = 'Previz'

def ids_iterator():
    for i in xrange(sys.maxint):
        yield i

ids=ids_iterator()

API_TOKEN_LABEL  = next(ids)
API_TOKEN_EDIT   = next(ids)
API_TOKEN_BUTTON = next(ids)

TEAM_LABEL     = next(ids)
TEAM_SELECT    = next(ids)
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

MSG_PUBLISH_DONE = __plugin_id__

SETTINGS_API_TOKEN = 'api_token'

def build_mock_data():
    team_id    = 0
    project_id = 0
    scene_id   = 0

    teams = []
    for t in range(1, 4):

        projects = []
        for p in range(1, 4):

            scenes = []
            for s in range(1, 4):
                scene_id += 1
                scenes.append({
                    'id': scene_id,
                    'name': 'Scene %d' % scene_id
                })

            project_id += 1
            projects.append({
                'id': project_id,
                'title': 'Project %d' % project_id,
                'scenes': scenes
            })

        team_id += 1
        teams.append({
            'id': team_id,
            'name': 'Team %d' % team_id,
            'projects': projects
        })
    return teams

teams = build_mock_data()

def key(x):
    key = 'name'
    if 'title' in x:
        key = 'title'
    return x[key]

def find_by_id(items, id):
    for item in items:
        if item['id'] == id:
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
            if SETTINGS_API_TOKEN not in shelf:
                shelf[SETTINGS_API_TOKEN] = ''

    def __getitem__(self, key):
        with self.open('r') as shelf:
            return shelf[key]

    def __setitem__(self, key, value):
        with self.open('w') as shelf:
            shelf[key] = value
            shelf.sync()

    def open(self, flag='r'):
        print self.path
        return contextlib.closing(shelve.open(self.path, flag))

    @property
    def path(self):
        return os.path.join(self.dirpath, 'settings')

    @property
    def dirpath(self):
        return os.path.join(storage.GeGetStartupWritePath(), self.namespace)

class PrevizDialog(gui.GeDialog):
    def __init__(self):
        self.settings = Settings(__plugin_title__)

        self.commands = {
            API_TOKEN_EDIT:     self.OnAPITokenChanged,
            API_TOKEN_BUTTON:   self.OnAPITokenButtonPressed,
            TEAM_SELECT:        self.OnTeamSelectPressed,
            PROJECT_SELECT:     self.OnProjectSelectPressed,
            SCENE_SELECT:       self.OnSceneSelectPressed,
            PROJECT_NEW_BUTTON: self.OnProjectNewButtonPressed,
            SCENE_NEW_BUTTON:   self.OnSceneNewButtonPressed,
            REFRESH_BUTTON:     self.OnRefreshButtonPressed,
            EXPORT_BUTTON:      self.OnExportButtonPressed,
            PUBLISH_BUTTON:     self.OnPublishButtonPressed
        }

    @property
    def previz_project(self):
        api_root = 'https://app.previz.co/api'
        api_token = self.GetString(API_TOKEN_EDIT)
        return previz.PrevizProject(api_root, api_token)

    def InitValues(self):
        print 'PrevizDialog.InitValues'

        self.SetString(API_TOKEN_EDIT, self.settings['api_token'])

        self.RefreshUI()

        return True

    def CreateLayout(self):
        self.SetTitle(__plugin_title__)

        self.CreateAPITokenLine()

        self.AddSeparatorH(1)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=2,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.CreateTeamLine()
        self.CreateProjectLine()
        self.CreateSceneLine()

        self.GroupEnd()

        self.AddSeparatorH(1)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=2,
                        title='Previz',
                        groupflags=c4d.BORDER_NONE)

        self.CreateNewProjectLine()
        self.CreateNewSceneLine()

        self.GroupEnd()

        self.AddSeparatorH(1)

        self.GroupBegin(id=next(ids),
                        flags=c4d.BFH_SCALEFIT,
                        cols=3,
                        rows=1,
                        title='Actions',
                        groupflags=c4d.BORDER_NONE)

        self.AddButton(id=REFRESH_BUTTON,
                       flags=c4d.BFH_SCALEFIT | c4d.BFV_BOTTOM,
                       name='Refresh')

        self.AddButton(id=EXPORT_BUTTON,
                       flags=c4d.BFH_SCALEFIT | c4d.BFV_BOTTOM,
                       name='Export to file')

        self.AddButton(id=PUBLISH_BUTTON,
                       flags=c4d.BFH_SCALEFIT | c4d.BFV_BOTTOM,
                       name='Publish to Previz')

        return True

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

    def CreateProjectLine(self):
        self.AddStaticText(id=PROJECT_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='Project')

        self.AddComboBox(id=PROJECT_SELECT,
                         flags=c4d.BFH_SCALEFIT)

    def CreateSceneLine(self):
        self.AddStaticText(id=SCENE_LABEL,
                           flags=c4d.BFH_LEFT,
                           name='Scene')

        self.AddComboBox(id=SCENE_SELECT,
                         flags=c4d.BFH_SCALEFIT)

    def CreateNewProjectLine(self):
        self.AddEditText(id=PROJECT_NEW_EDIT,
                         flags=c4d.BFH_SCALEFIT)

        self.AddButton(id=PROJECT_NEW_BUTTON,
                       flags=c4d.BFH_FIT,
                       name='New project')

    def CreateNewSceneLine(self):
        self.AddEditText(id=SCENE_NEW_EDIT,
                         flags=c4d.BFH_SCALEFIT)

        self.AddButton(id=SCENE_NEW_BUTTON,
                       flags=c4d.BFH_FIT,
                       name='New scene')

    def CoreMessage(self, id, msg):
        if id != MSG_PUBLISH_DONE:
            return gui.GeDialog.CoreMessage(self, id, msg)

        print 'PrevizDialog.CoreMessage', id, id == __plugin_id__
        self.RefreshUI()
        return True

    def Command(self, id, msg):
        print 'PrevizDialog.Command', id, msg

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

    def OnAPITokenChanged(self, msg):
        print 'PrevizDialog.OnAPITokenChanged'
        token = self.GetString(API_TOKEN_EDIT)
        self.settings[SETTINGS_API_TOKEN] = token

    def OnAPITokenButtonPressed(self, msg):
        print 'PrevizDialog.OnAPITokenButtonPressed', msg
        webbrowser.open('https://app.previz.co/settings#/api')

    def OnTeamSelectPressed(self, msg):
        print 'PrevizDialog.OnTeamSelectPressed', msg
        self.SwitchTeam()
        self.RefreshTeamComboBox()

    def SwitchTeam(self):
        team_id = self.GetInt32(TEAM_SELECT)
        self.previz_project.switch_team(team_id)

        global teams
        teams = self.previz_project.get_all()

    def OnProjectSelectPressed(self, msg):
        print 'PrevizDialog.OnProjectSelectPressed', msg
        self.RefreshSceneComboBox()

    def OnSceneSelectPressed(self, msg):
        print 'PrevizDialog.OnSceneSelectPressed', msg

    def OnRefreshButtonPressed(self, msg):
        print 'PrevizDialog.OnRefreshButtonPressed', msg
        self.RefreshTeamComboBox()

    def RefreshTeamComboBox(self):
        print 'PrevizDialog.RefreshTeamComboBox'

        with Restore(self.GetInt32, self.SetInt32, TEAM_SELECT) as touch:
            self.FreeChildren(TEAM_SELECT)

            global teams
            #teams = self.previz_project.get_all()

            for team in sorted(teams, key=key):
                id   = team['id']
                name = team['name']
                self.AddChild(TEAM_SELECT, id, name)
                touch(id)

        self.LayoutChanged(TEAM_SELECT)

        print 'RefreshTeamComboBox', touch.old_value, self.GetInt32(TEAM_SELECT)
        self.RefreshProjectComboBox()

    def RefreshProjectComboBox(self):
        print 'PrevizDialog.RefreshProjectComboBox'

        with Restore(self.GetInt32, self.SetInt32, PROJECT_SELECT) as touch:
            self.FreeChildren(PROJECT_SELECT)

            global teams
            team     = find_by_id(teams, self.GetInt32(TEAM_SELECT))
            projects = [] if team is None else team['projects']

            for project in projects:
                id   = project['id']
                title = project['title']
                self.AddChild(PROJECT_SELECT, id, title)
                touch(id)

        self.LayoutChanged(PROJECT_SELECT)

        self.RefreshSceneComboBox()

    def RefreshSceneComboBox(self):
        print 'PrevizDialog.RefreshSceneComboBox'

        with Restore(self.GetInt32, self.SetInt32, SCENE_SELECT) as touch:
            self.FreeChildren(SCENE_SELECT)

            global teams
            team     = find_by_id(teams, self.GetInt32(TEAM_SELECT))
            projects = [] if team is None else team['projects']
            project  = find_by_id(projects, self.GetInt32(PROJECT_SELECT))
            scenes   = [] if project is None else project['scenes']

            for scene in scenes:
                id = scene['id']
                name = scene['name']
                self.AddChild(SCENE_SELECT, id, name)
                touch(id)

        self.LayoutChanged(SCENE_SELECT)

    def OnProjectNewButtonPressed(self, msg):
        print 'PrevizDialog.OnProjectNewButtonPressed', msg

        # New project

        project_name = self.GetString(PROJECT_NEW_EDIT)
        project = self.previz_project.new_project(project_name)

        # Clear project name
        # For some reason SetString doesn't send an event

        self.SetString(PROJECT_NEW_EDIT, '')
        self.RefreshProjectNewButton()

        # Select new project

        self.RefreshProjectComboBox()
        self.SetInt32(PROJECT_SELECT, project['id'])

    def OnSceneNewButtonPressed(self, msg):
        print 'PrevizDialog.OnSceneNewButtonPressed', msg
        print '==== Not Implemented Yet ===='

    def OnExportButtonPressed(self, msg):
        print 'PrevizDialog.OnExportButtonPressed', msg

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
        print 'PrevizDialog.OnPublishButtonPressed', msg

        # Write JSON to disk
        scene = BuildPrevizScene()

        fp, path = tempfile.mkstemp(prefix='previz-',
                                    suffix='.json',
                                    text=True)
        fp = os.fdopen(fp)
        previz.export(scene, fp)
        fp.close()

        # Upload JSON to Previz in a thread
        api_root = 'https://app.previz.co/api'
        api_token = self.GetString(API_TOKEN_EDIT)
        project_id = self.GetInt32(PROJECT_SELECT)

        global publisher_thread
        publisher_thread = PublisherThread(api_root, api_token, project_id, path)
        publisher_thread.Start()

        # Notice user of success

    def RefreshUI(self):
        print 'PrevizDialog.RefreshUI'

        self.RefreshProjectNewButton()
        self.RefreshSceneNewButton()
        self.RefreshRefreshButton()
        self.RefreshPublishButton()

    def RefreshProjectNewButton(self):
        team_id = self.GetInt32(TEAM_SELECT)
        team_id_is_valid = team_id >= 1

        project_name = self.GetString(PROJECT_NEW_EDIT)
        project_name_is_valid = len(project_name) > 0

        self.Enable(PROJECT_NEW_BUTTON,
                    team_id_is_valid \
                    and project_name_is_valid)

    def RefreshSceneNewButton(self):
        project_id = self.GetInt32(PROJECT_SELECT)
        project_id_is_valid = project_id >= 1

        scene_name = self.GetString(SCENE_NEW_EDIT)
        scene_name_is_valid = len(scene_name) > 0

        self.Enable(SCENE_NEW_BUTTON,
                    project_id_is_valid \
                    and scene_name_is_valid)

    def RefreshRefreshButton(self):
        api_token = self.GetString(API_TOKEN_EDIT)
        is_api_token_valid = len(api_token) > 0
        self.Enable(REFRESH_BUTTON, is_api_token_valid)

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
                    and not is_publisher_thread_running)


class PrevizCommandData(plugins.CommandData):
    dialog = None

    def Execute(self, doc):
        self.init_dialog_if_needed()
        return self.dialog.Open(dlgtype=c4d.DLG_TYPE_ASYNC,
                                pluginid=__plugin_id__,
                                defaultw=250,
                                defaulth=50)

    def RestoreLayout(self, sec_ref):
        self.init_dialog_if_needed()
        return self.dialog.Restore(pluginid=__plugin_id__,
                                   secret=sec_ref)

    def Message(self, type, data):
        #print 'PrevizCommandData.Message', type, data
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

        for uvtag, uvset in zip(uvtags, uvsets):
            uvdict = uvtag.GetSlow(polygon_index)
            for vn in vertex_names(p):
                uv = list((uvdict[vn].x, 1-uvdict[vn].y))
                uvset.coordinates.append(uv)
                faces.append(uv_index)
                uv_index += 1

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
    print '---- START', 'BuildPrevizScene'

    doc = c4d.documents.GetActiveDocument()
    doc = doc.Polygonize()

    return previz.Scene('Cinema4D-Previz',
                        os.path.basename(doc.GetDocumentPath()),
                        None,
                        build_objects(doc))

    print '---- END', 'BuildPrevizScene'


class PublisherThread(threading.C4DThread):
    def __init__(self, api_root, api_token, project_id, path):
        self.api_root = api_root
        self.api_token = api_token
        self.project_id = project_id
        self.path = path

    def Main(self):
        p = previz.PrevizProject(self.api_root,
                                 self.api_token,
                                 self.project_id)
        with open(self.path, 'rb') as fp:
            print 'START upload'
            p.update_scene(fp)
            print 'STOP upload'
        c4d.SpecialEventAdd(MSG_PUBLISH_DONE)

publisher_thread = None

def make_callback(text):
    def func(data):
        print text
    return func

plugin_messages = {
    c4d.C4DPL_STARTACTIVITY:       make_callback('C4DPL_STARTACTIVITY'),
    c4d.C4DPL_ENDACTIVITY:         make_callback('C4DPL_ENDACTIVITY'),
    c4d.C4DPL_RELOADPYTHONPLUGINS: make_callback('C4DPL_RELOADPYTHONPLUGINS'),
    c4d.C4DPL_COMMANDLINEARGS:     make_callback('C4DPL_COMMANDLINEARGS'),
    c4d.C4DPL_BUILDMENU:           make_callback('C4DPL_BUILDMENU'),
    c4d.C4DPL_ENDPROGRAM:          make_callback('C4DPL_ENDPROGRAM'),
    c4d.C4DPL_PROGRAM_STARTED:     make_callback('C4DPL_PROGRAM_STARTED')
}


def PluginMessage(id, data):
    cb = plugin_messages.get(id)
    print 'PluginMessage', id, data, cb
    if cb is None:
        return False
    cb(data)
    return True

if __name__ == '__main__':
    print 'Registering PrevizCommandData'
    plugins.RegisterCommandPlugin(id=__plugin_id__,
                                  str='Py-Previz',
                                  help='Py - Previz',
                                  info=0,
                                  dat=PrevizCommandData(),
                                  icon=None)
