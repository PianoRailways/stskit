"""
abstraktes slotgrafik-fenster

die slotgrafik besteht aus einem balkendiagramm das die belegung von einzelnen gleisen durch züge im lauf der zeit darstellt.

spezifische implementationen sind die gleisbelegungs-, einfahrts- und ausfahrtstabellen.
"""

from dataclasses import dataclass, field
import matplotlib as mpl
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
from matplotlib.figure import Figure
import numpy as np
from PyQt5 import Qt, QtCore, QtGui, QtWidgets
import re
from typing import Any, Dict, Generator, Iterable, List, Mapping, Optional, Set, Tuple, Union

from auswertung import StsAuswertung
from anlage import Anlage
from stsplugin import PluginClient
from stsobj import FahrplanZeile, ZugDetails, time_to_minutes

mpl.use('Qt5Agg')


def hour_minutes_formatter(x: Union[int, float], pos: Any) -> str:
    # return "{0:02}:{1:02}".format(int(x) // 60, int(x) % 60)
    return f"{int(x) // 60:02}:{int(x) % 60:02}"


def gleisname_sortkey(gleis: str) -> Tuple[str, int, str]:
    """
    gleisname in sortierschlüssel umwandeln

    annahme: gleisname setzt sich aus präfix, nummer und suffix zusammen.
    präfix und suffix bestehen aus buchstaben und leerzeichen oder fehlen ganz.
    präfix und suffix können durch leerzeichen von der nummer abgetrennt sein, müssen aber nicht.

    :param gleis: gleisname, wie er im fahrplan der züge steht
    :return: tupel (präfix, nummer, suffix). leerzeichen entfernt.
    """
    expr = r"([a-zA-Z ]*)([0-9]*)([a-zA-Z ]*)"
    mo = re.match(expr, gleis)
    prefix = mo.group(1).replace(" ", "")
    try:
        nummer = int(mo.group(2))
    except ValueError:
        nummer = 0
    suffix = mo.group(3).replace(" ", "")
    return prefix, nummer, suffix


# farben = {g: mpl.colors.TABLEAU_COLORS[i % len(mpl.colors.TABLEAU_COLORS)]
#           for i, g in enumerate(self.client.zuggattungen)}
# colors = [farben[b[5]] for b in bars]
farben = [k for k in mpl.colors.TABLEAU_COLORS]


# colors = [farben[i % len(farben)] for i in range(len(bars))]

# colors = [farben[slot['zug'].nummer // 10000] for slot in slots]


@dataclass
class Slot:
    """
    repräsentation eines zugslots im belegungsplan.

    dieses objekt enthält alle daten für die darstellung in der slotgrafik.
    die daten sind fertig verarbeitet, zugpaarung ist eingetragen, konflikte sind markiert oder gelöst.

    properties berechnen gewisse statische darstellungsmerkmale wie farben.
    """
    zug: ZugDetails
    plan: FahrplanZeile
    gleis: str = ""
    zeit: int = 0
    dauer: int = 0
    kuppelzug: Optional[ZugDetails] = None
    konflikte: List['Slot'] = field(default_factory=list)

    def __eq__(self, other):
        return self.zug.name == other.zug.name and self.gleis == other.gleis

    def __hash__(self):
        return hash((self.gleis, self.zug.name))

    @property
    def farbe(self) -> str:
        """
        hintergrundfarbe aus zugpriorität

        die hintergrundfarbe markiert die priorität eines zuges:
        hochgeschwindigkeitszüge vor fernverkehr vor regionalverkehr vor güterverkehr.

        im moment wird die priorität aus dem zugnamen und der zugnummer abgeleitet.
        das verfahren muss noch verfeinert und auf die verschiedenen regionen abgestimmt werden.

        :return: farbbezeichnung für matplotlib
        """
        if self.zug.gattung in {'ICE', 'TGV'}:
            return 'tab:orange'
        elif self.zug.gattung in {'IC', 'EC', 'IR', 'IRE'}:
            return 'tab:green'
        elif self.zug.gattung in {'RE', 'RB'}:
            return 'tab:blue'
        elif self.zug.gattung in {'S'}:
            return 'tab:purple'
        elif nummer := self.zug.nummer > 0:
            if nummer < 2000:
                return 'tab:green'
            elif nummer < 10000:
                return 'tab:blue'
            elif nummer < 30000:
                return 'tab:purple'
            else:
                return 'tab:brown'
        else:
            return 'tab:gray'

    @property
    def randfarbe(self) -> str:
        """
        randfarbe markiert konflikte und kuppelvorgänge

        :return: farbbezeichnung für matplotlib
        """
        if self.konflikte:
            return 'r'
        elif self.kuppelzug:
            return 'g'
        else:
            return 'k'

    @property
    def titel(self) -> str:
        """
        "zugname (verspätung)"

        :return: (str) zugtitel
        """
        if self.zug.verspaetung:
            return f"{self.zug.name} ({self.zug.verspaetung:+})"
        else:
            return f"{self.zug.name}"

    @property
    def style(self) -> str:
        """
        schriftstil markiert halt oder durchfahrt

        :return: "normal" oder "italic"
        """
        return "italic" if self.plan.durchfahrt() else "normal"


class SlotWindow(QtWidgets.QMainWindow):
    """
    gemeinsamer vorfahr für slotdiagrammfenster

    nachfahren implementieren die slots_erstellen- und konflikte_loesen-methoden.
    """

    def __init__(self):
        super().__init__()
        self.client: Optional[PluginClient] = None
        self.anlage: Optional[Anlage] = None
        self.auswertung: Optional[StsAuswertung] = None

        self.setWindowTitle("slot-grafik")
        self._main = QtWidgets.QWidget()
        self.setCentralWidget(self._main)
        layout = QtWidgets.QVBoxLayout(self._main)

        canvas = FigureCanvas(Figure(figsize=(5, 3)))
        layout.addWidget(canvas)
        self._axes = canvas.figure.subplots()
        self._balken = None
        self._labels = []
        self._zugdetails: mpl.text.Text = None

        self._gleise: List[str] = []
        self._slots: List[Slot] = []
        self._gleis_slots: Dict[str, List[Slot]] = {}

        self.zeitfenster_voraus = 55
        self.zeitfenster_zurueck = 5

        canvas.mpl_connect("pick_event", self.on_pick)

    def update(self):
        self.daten_update()
        self.grafik_update()

    def daten_update(self):
        self._slots = []
        self._gleis_slots = {}
        self._gleise = []

        for slot in self.slots_erstellen():
            try:
                slots = self._gleis_slots[slot.gleis]
            except KeyError:
                slots = self._gleis_slots[slot.gleis] = []
            if slot not in slots:
                slots.append(slot)

        for gleis, slots in self._gleis_slots.items():
            self.konflikte_loesen(gleis, slots)

        self._gleise = sorted(self._gleis_slots.keys(), key=gleisname_sortkey)
        self._slots = []
        for slots in self._gleis_slots.values():
            self._slots.extend(slots)

    def slots_erstellen(self) -> Generator[Slot, None, None]:
        pass

    def konflikte_loesen(self, gleis: str, slots: List[Slot]) -> List[Slot]:
        pass

    def grafik_update(self):
        self._axes.clear()

        kwargs = dict()
        kwargs['align'] = 'center'
        kwargs['alpha'] = 0.5
        kwargs['width'] = 1.0

        x_labels = self._gleise
        x_labels_pos = list(range(len(x_labels)))
        x_pos = np.asarray([self._gleise.index(slot.gleis) for slot in self._slots])
        y_bot = np.asarray([slot.zeit for slot in self._slots])
        y_hgt = np.asarray([slot.dauer for slot in self._slots])
        labels = [slot.titel for slot in self._slots]
        colors = [slot.farbe for slot in self._slots]
        style = [slot.style for slot in self._slots]
        edgecolors = [slot.randfarbe for slot in self._slots]
        linewidth = [w for w in map(lambda f: 1 if f == 'k' else 2, edgecolors)]

        self._axes.set_xticks(x_labels_pos, x_labels, rotation=45, horizontalalignment='right')
        self._axes.yaxis.set_major_formatter(hour_minutes_formatter)
        self._axes.yaxis.set_minor_locator(mpl.ticker.MultipleLocator(1))
        self._axes.yaxis.set_major_locator(mpl.ticker.MultipleLocator(10))
        self._axes.yaxis.grid(True, which='major')
        self._axes.xaxis.grid(True)

        zeit = time_to_minutes(self.client.calc_simzeit())
        self._axes.set_ylim(bottom=zeit + self.zeitfenster_voraus, top=zeit - self.zeitfenster_zurueck, auto=False)

        self._balken = self._axes.bar(x_pos, y_hgt, bottom=y_bot, data=None, color=colors, edgecolor=edgecolors,
                                      linewidth=linewidth, picker=True, **kwargs)
        self._labels = self._axes.bar_label(self._balken, labels=labels, label_type='center',
                                            fontsize='small', fontstretch='condensed')

        if self.zeitfenster_zurueck > 0:
            self._axes.axhline(y=zeit)

        self._axes.figure.tight_layout()

        self._zugdetails = self._axes.text(1, zeit, 'leerfahrt', bbox={'facecolor': 'yellow', 'alpha': 0.5},
                                           fontsize='small', fontstretch='condensed', visible=False)

        self._axes.figure.canvas.draw()

    def get_slot_hint(self, slot: Slot):
        gleise = [fpz.gleis for fpz in slot.zug.fahrplan if fpz.gleis]
        if slot.zug.von:
            gleise.insert(0, slot.zug.von)
        if slot.zug.nach:
            gleise.append(slot.zug.nach)
        weg = " - ".join(gleise)
        return "\n".join([slot.titel, weg])

    def on_pick(self, event):
        if event.mouseevent.inaxes == self._axes:
            gleis = self._gleise[round(event.mouseevent.xdata)]
            zeit = event.mouseevent.ydata
            text = []
            ymin = 24 * 60
            ymax = 0
            if isinstance(event.artist, mpl.patches.Rectangle):
                for slot in self._gleis_slots[gleis]:
                    if slot.zeit <= zeit <= slot.zeit + slot.dauer:
                        ymin = min(ymin, slot.zeit)
                        ymax = max(ymax, slot.zeit + slot.dauer)
                        text.append(self.get_slot_hint(slot))
                self._zugdetails.set(text="\n".join(text), visible=True, x=self._gleise.index(gleis),
                                     y=(ymin + ymax) / 2)
                self._axes.figure.canvas.draw()
        else:
            # im mouseclick event behandeln
            self._zugdetails.set_visible(False)
            self._axes.figure.canvas.draw()
