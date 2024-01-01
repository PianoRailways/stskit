import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Mapping, Optional, Set, Tuple, Union

import networkx as nx

from stskit.interface.stsgraph import GraphClient
from stskit.interface.stsobj import Knoten, AnlagenInfo
from stskit.graphs.signalgraph import SignalGraph
from stskit.graphs.bahnhofgraph import BahnhofGraph, BahnsteigGraph
from stskit.graphs.liniengraph import LinienGraph, LinienGraphEdge
from stskit.graphs.zielgraph import ZielGraph
from stskit.utils.gleisnamen import default_anschlussname, default_bahnhofname, default_bahnsteigname
from stskit.utils.export import json_object_hook
from stskit.zugschema import Zugschema


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def bahnhofgraph_konfig_umdrehen(gleis_konfig, anschluss_konfig):
    """
    Konfiguration invertieren

    bahnhofgraph_konfigurien benoetigt eine bottom-up Struktur,
    waehrend die Konfigurationsdatei eine top-down Struktur hat.
    diese Methode wandelt top-down in bottom-up um.
    """

    result = {}

    for bf, bf_dict in gleis_konfig.items():
        for bft, bft_dict in bf_dict.items():
            for bs, bs_set in bft_dict.items():
                for gl in bs_set:
                    result[('Gl', gl)] = (bf, bft, bs)

    for anst, anst_set in anschluss_konfig.items():
        for agl in anst_set:
            result[('Agl', agl)] = (anst,)

    return result


class Anlage:
    def __init__(self):
        self.anlageninfo: Optional[AnlagenInfo] = None
        self.config: Dict[str, Any] = {}

        self.signalgraph = SignalGraph()
        self.bahnsteiggraph = BahnsteigGraph()
        self.bahnhofgraph = BahnhofGraph()
        self.liniengraph = LinienGraph()
        self.zielgraph = ZielGraph()

        # todo : streckenkonfiguration
        # todo : streckenmarkierung

        self.strecken: Dict[Tuple[Tuple[str, str], Tuple[str, str]], List[Tuple[str, str]]] = {}
        self.hauptstrecke: Optional[Tuple[Tuple[str, str], Tuple[str, str]]] = None
        self.streckenmarkierung: Dict[Tuple[Tuple[str, str], Tuple[str, str]], str] = {}
        self.gleissperrungen: Set[Tuple[str, str]] = set()

        self.zugschema = Zugschema()

    def update(self, client: GraphClient, config_path: os.PathLike):
        # todo : konfiguration speichern

        if self.anlageninfo is None:
            self.anlageninfo = client.anlageninfo

        if not self.config and config_path:
            config_path = Path(config_path)
            default_path = Path(__file__).parent.parent / "config"
            try:
                logger.info(f"Konfiguration laden von {config_path}")
                self.load_config(config_path)
            except OSError:
                logger.warning("Keine benutzerspezifische Anlagenkonfiguration gefunden")
                logger.info(f"Beispielkonfiguration laden von {default_path}")
                try:
                    self.load_config(default_path)
                except OSError:
                    logger.warning("Keine Beispielkonfiguration gefunden")
            except ValueError as e:
                logger.exception("Fehlerhafte Anlagenkonfiguration")

            Zugschema.find_schemas(default_path)
            Zugschema.find_schemas(config_path)
            self.zugschema.load_config(self.config.get('zugschema', ''), self.anlageninfo.region)

        if not self.signalgraph:
            self.signalgraph = client.signalgraph.copy(as_view=False)
        if not self.bahnsteiggraph:
            self.bahnsteiggraph = client.bahnsteiggraph.copy(as_view=False)
        if not self.bahnhofgraph and self.signalgraph and self.bahnsteiggraph:
            self.bahnhofgraph.import_anlageninfo(self.anlageninfo)
            self.bahnhofgraph.import_bahnsteiggraph(self.bahnsteiggraph, default_bahnsteigname, default_bahnhofname)
            self.bahnhofgraph.import_signalgraph(self.signalgraph, default_anschlussname)
            try:
                self.bahnhofgraph.konfigurieren(self.config['bahnhofgraph'])
            except KeyError:
                logger.warning("keine bahnhofkonfiguration gefunden")

        # todo : zielgraph kann sich zur laufzeit aendern
        self.zielgraph = client.zielgraph.copy(as_view=True)

        if not self.liniengraph and self.bahnhofgraph and self.zielgraph:
            self.liniengraph_konfigurieren()
            self.liniengraph_mit_signalgraph_abgleichen()

        if len(self.strecken) == 0 and self.liniengraph:
            strecken = self.liniengraph.strecken_vorschlagen(2, 3)
            for strecke in strecken:
                key = (strecke[0], strecke[-1])
                self.strecken[key] = strecke
            self.strecken_konfigurieren()

    def label_aus_zielgleis(self, gleis: Union[int, str]) -> Tuple[str, str]:
        if isinstance(gleis, int):
            signal_node = self.signalgraph.nodes[gleis]
            return 'Agl', signal_node.name
        else:
            return 'Gl', gleis

    def liniengraph_konfigurieren(self):
        """
        benoetigt zielgraph
        """

        for node1, node2, kante in self.zielgraph.edges(data=True):
            if kante.typ == 'P':
                ziel1_data = self.zielgraph.nodes[node1]
                ziel2_data = self.zielgraph.nodes[node2]
                bst1 = self.bahnhofgraph.find_superior(self.label_aus_zielgleis(ziel1_data.plan), {'Bf', 'Anst'})
                bst2 = self.bahnhofgraph.find_superior(self.label_aus_zielgleis(ziel2_data.plan), {'Bf', 'Anst'})
                bst1_data = self.bahnhofgraph.nodes[bst1]
                bst2_data = self.bahnhofgraph.nodes[bst2]
                self.liniengraph.linie_eintragen(ziel1_data, bst1_data, ziel2_data, bst2_data)

    def liniengraph_mit_signalgraph_abgleichen(self):
        bearbeiten = {(ziel1, ziel2): kante for ziel1, ziel2, kante in self.liniengraph.edges(data=True)}

        while bearbeiten:
            ziel1, ziel2 = next(iter(bearbeiten))
            kante = bearbeiten[(ziel1, ziel2)]
            del bearbeiten[(ziel1, ziel2)]

            try:
                gleis1 = sorted(self.bahnhofgraph.list_children(ziel1, {'Gl', 'Agl'}))[0]
            except IndexError:
                continue
            gleis1_data = self.bahnhofgraph.nodes[gleis1]
            signal1 = gleis1_data.enr if gleis1_data.typ == "Agl" else gleis1_data.name

            try:
                gleis2 = sorted(self.bahnhofgraph.list_children(ziel2, {'Gl', 'Agl'}))[0]
            except IndexError:
                continue
            gleis2_data = self.bahnhofgraph.nodes[gleis2]
            signal2 = gleis2_data.enr if gleis2_data.typ == "Agl" else gleis2_data.name

            signal_strecke = nx.shortest_path(self.signalgraph, signal1, signal2)

            for signal in signal_strecke[1:-1]:
                signal_data = self.signalgraph.nodes[signal]
                if signal_data.typ in {Knoten.TYP_NUMMER["Bahnsteig"], Knoten.TYP_NUMMER["Haltepunkt"]}:
                    zwischenziel = self.bahnhofgraph.find_superior(('Gl', signal_data.name), {'Bf', 'Agl'})

                    neue_kante = LinienGraphEdge()
                    neue_kante.update(kante)
                    neue_kante.fahrzeit_max = kante.fahrzeit_max / 2
                    neue_kante.fahrzeit_min = kante.fahrzeit_min / 2
                    neue_kante.fahrzeit_summe = kante.fahrzeit_summe / 2
                    neue_kante.fahrzeit_schnitt = kante.fahrzeit_schnitt / 2

                    if not self.liniengraph.has_edge(ziel1, zwischenziel):
                        self.liniengraph.add_edge(ziel1, zwischenziel, **neue_kante)
                        bearbeiten[(ziel1, zwischenziel)] = neue_kante

                    if not self.liniengraph.has_edge(zwischenziel, ziel2):
                        self.liniengraph.add_edge(zwischenziel, ziel2, **neue_kante)
                        bearbeiten[(zwischenziel, ziel2)] = neue_kante

                    self.liniengraph.remove_edge(ziel1, ziel2)

    def strecken_konfigurieren(self):
        for titel, konfig in self.config['strecken']:
            strecke = []
            for name in konfig:
                if self.bahnhofgraph.has_node(node := ('Bf', name)):
                    strecke.append(node)
                elif self.bahnhofgraph.has_node(node := ('Anst', name)):
                    strecke.append(node)
            key = (strecke[0], strecke[-1])
            self.strecken[key] = strecke

            if titel == self.config['hauptstrecke']:
                self.hauptstrecke = key

        for namen, markierung in self.config['streckenmarkierung']:
            node1 = self.bahnhofgraph.find_name(namen[0])
            node2 = self.bahnhofgraph.find_name(namen[1])
            if node1 is not None and node2 is not None:
                self.streckenmarkierung[(node1, node2)] = markierung

    def load_config(self, path: os.PathLike, load_graphs=False, ignore_version=False):
        """

        :param path: verzeichnis mit den konfigurationsdaten.
            der dateiname wird aus der anlagen-id gebildet.
        :param load_graphs: die graphen werden normalerweise vom simulator abgefragt und erstellt.
            für offline-auswertung können sie auch aus dem konfigurationsfile geladen werden.
        :return: None
        :raise: OSError, JSONDecodeError(ValueError)
        """
        if load_graphs:
            p = Path(path) / f"{self.anlageninfo.aid}diag.json"
        else:
            p = Path(path) / f"{self.anlageninfo.aid}.json"

        with open(p, encoding='utf-8') as fp:
            d = json.load(fp, object_hook=json_object_hook)

        if not ignore_version:
            assert d['_aid'] == self.anlageninfo.aid
            if self.anlageninfo.build != d['_build']:
                logger.warning(f"unterschiedliche build-nummern (file: {d['_build']}, sim: {self.anlageninfo.build})")
            if '_version' not in d:
                d['_version'] = 1
                logger.warning(f"konfigurationsdatei ohne versionsangabe. nehme 1 an.")
            if d['_version'] < 2:
                logger.error(f"inkompatible konfigurationsdatei - auto-konfiguration")
                return

        if d['_version'] == 2:
            self.set_config_v2(d)
        elif d['_version'] == 3:
            self.config = d

    def set_config_v2(self, d: Dict):
        def _find_sektor(gleis: str, sektoren_gleise: Dict) -> Optional[str]:
            for bahnsteig, gleise in sektoren_gleise.items():
                if gleis in gl:
                    return bahnsteig
            else:
                return None

        gleis_konfig = {}
        try:
            sektoren = d['sektoren']
        except KeyError:
            logger.info("Fehlende Sektoren-Konfiguration")
            sektoren = {}

        try:
            for bf, gleise in d['bahnsteiggruppen'].items():
                for gl in gleise:
                    if bf not in gleis_konfig:
                        gleis_konfig[bf] = {bf: {}}
                    bs = _find_sektor(gl, sektoren)
                    if not bs:
                        bs = gl
                    try:
                        gleis_konfig[bf][bf][bs].add(gl)
                    except KeyError:
                        gleis_konfig[bf][bf][bs] = {gl}
        except KeyError:
            logger.info("Fehlende Bahnsteiggruppen-Konfiguration")

        try:
            anschluss_konfig = d['anschlussgruppen']
        except KeyError:
            logger.info("Fehlende Anschlussgruppen-Konfiguration")
            anschluss_konfig = {}

        self.config['bahnhofgraph'] = bahnhofgraph_konfig_umdrehen(gleis_konfig, anschluss_konfig)

        try:
            self.config['strecken'] = d['strecken']
        except KeyError:
            logger.info("Fehlende Streckenkonfiguration")
        try:
            self.config['hauptstrecke'] = d['hauptstrecke']
        except KeyError:
            logger.info("Keine Hauptstrecke konfiguriert")
        try:
            markierungen = d['streckenmarkierung']
        except KeyError:
            logger.info("keine streckenmarkierungen konfiguriert")
        else:
            streckenmarkierung = {}
            for markierung in markierungen:
                try:
                    streckenmarkierung[(markierung[0], markierung[1])] = markierung[2]
                except IndexError:
                    pass
            self.config['streckenmarkierung'] = streckenmarkierung

        try:
            self.config['zugschema'] = d['zugschema']
        except KeyError:
            self.config['zugschema'] = ''
