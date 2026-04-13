# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "caveclient>=8.0.1",
#     "ipykernel>=7.2.0",
#     "ipywidgets>=8.1.8",
#     "neuroglancer>=2.41.2",
#     "nglui>=4.7.1",
#     "pillow>=12.1.1",
#     "selenium>=4.41.0",
# ]
# ///


# %%
import concurrent.futures
from io import BytesIO
from pprint import pprint
from webbrowser import open as open_browser

from caveclient import CAVEclient
from neuroglancer import credentials_provider
from neuroglancer.default_credentials_manager import default_credentials_manager
from neuroglancer.webdriver import Webdriver
from nglui.statebuilder import ViewerState
from PIL import Image


class MiddleAuthCredentialsProvider(credentials_provider.CredentialsProvider):
    def __init__(self, token):
        super().__init__()
        self._token = token

    def get_new(self):
        f = concurrent.futures.Future()
        f.set_result({"tokenType": "Bearer", "accessToken": self._token})
        return f


default_credentials_manager.register(
    "middleauthapp",
    lambda parameters: MiddleAuthCredentialsProvider(token),
)


client = CAVEclient("minnie65_public")
token = client.auth.token  # from your CAVEclient

states = [
    5560658513887232,
    "https://spelunker.cave-explorer.org/#!middleauth+https://global.daf-apis.com/nglstate/api/v1/4661635053518848",
    6730980495720448,
    5480005504073728,
    5791016970878976,
    4916101933694976,
    "https://spelunker.cave-explorer.org/#!middleauth+https://global.daf-apis.com/nglstate/api/v1/5754615176888320",
]
states = [
    "https://spelunker.cave-explorer.org/#!middleauth+https://global.daf-apis.com/nglstate/api/v1/4672168964128768"
]
headless = True
for state in states:
    if isinstance(state, str):
        state = int(state.split("/")[-1])

    state_info = client.state.get_state_json(state)

    state_info.pop("selection", None)

    pprint(state_info)

    vs = ViewerState(base_state=state_info, interactive=True)

    viewer = vs.viewer
    with viewer.txn() as s:
        s.show_axis_lines = False
        s.layout.orthographic_projection = True
        s.show_scale_bar = False
        s.showDefaultAnnotations = False
        s.layout = "3d"
        s.selected_layer = None

        # s.projection_background_color = "#ffffff"

    if headless:
        webdriver = Webdriver(viewer, headless=True, browser="chrome")
    else:
        open_browser(viewer.get_viewer_url())

    screenshot = viewer.screenshot(include_depth=False, size=(1000, 1000))

    img = Image.open(BytesIO(screenshot.screenshot.image))
    img.save(f"state_{state}.png")
