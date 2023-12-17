"""
schematischer netzplan (experimentell)

dieses modul ist in entwicklung.
"""

import logging
import sys
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple, Union

import matplotlib as mpl
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure
import networkx as nx
import netgraph
from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import pyqtSlot

from stskit.interface.stsplugin import PluginClient
from stskit.anlage import Anlage
import stskit.anlage as anlage
from stskit.zentrale import DatenZentrale
from stskit.qt.ui_gleisnetz import Ui_GleisnetzWindow


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


def graph_nachbarbahnsteige_vereinen(g: nx.DiGraph) -> nx.DiGraph:
    while True:
        for u, v, t in g.edges(data="typ", default="unkannt"):
            if t == "nachbar":
                g = nx.contracted_nodes(g, u, v, self_loops=False, copy=False)
                break
        else:
            break

    return g


class SignalGraph:
    def __init__(self):
        self.canvas = FigureCanvas(Figure(figsize=(5, 3)))
        self.axes = self.canvas.figure.subplots()
        self.graph = None

    def draw_graph(self, graph: nx.Graph, bahnsteig_graph: nx.Graph, filters: Optional[Iterable[Callable]] = None):
        self.axes.clear()

        graph = graph.to_undirected()
        graph.add_edges_from(bahnsteig_graph.edges, typ="nachbar")
        if filters is None:
            filters = []
        for filt in filters:
            graph = filt(graph)

        def fino(node):
            typ = graph.nodes[node]["typ"]
            return typ in {6, 7}

        sub_nodes = sorted([x for x, y in graph.nodes(data=True) if y.get('typ', -1) in {6, 7}])
        sub_graph = nx.subgraph(graph, sub_nodes).copy()
        # for x, y in zip(sub_nodes, sub_nodes[1:] + [sub_nodes[0]]):
        #     sub_graph.add_edge(x, y, typ='hilfslinie', distanz=1)
        sub_edges = list(zip(sub_nodes, sub_nodes[1:] + [sub_nodes[0]]))
        layout = netgraph.get_circular_layout(sub_edges)

        colormap = {2: "tab:blue",  # Signal
                    3: "tab:gray",  # Weiche unten
                    4: "tab:gray",  # Weiche oben
                    5: "tab:red",  # Bahnsteig
                    6: "tab:pink",  # Einfahrt
                    7: "tab:purple",  # Ausfahrt
                    12: "tab:orange"}  # Haltepunkt

        node_colors = {key: colormap.get(typ, "r") for key, typ in graph.nodes(data='typ', default='kein')}

        node_labels = {key: data["name"] for key, data in graph.nodes(data=True) if data.get('typ', -1) in {5, 6, 7, 12}}

        edge_labels = {(e1, e2): distanz
                       for e1, e2, distanz in graph.edges(data='distanz', default=0)
                       if distanz > 0}
        edge_length = {(e1, e2): max(1/100, zeit / 100)
                       for e1, e2, zeit in graph.edges(data='distanz', default=0)}

        # node_size=3
        # node_edge_width
        node_label_fontdict = {"size": 10}
        edge_label_fontdict = {"size": 10, "bbox": {"boxstyle": "circle",
                                                    "fc": mpl.rcParams["axes.facecolor"],
                                                    "ec": mpl.rcParams["axes.facecolor"]}}
        self.graph = netgraph.InteractiveGraph(graph, ax=self.axes,
                                      node_layout="spring",
                                      # node_layout_kwargs=dict(node_positions=layout),
                                      node_color=node_colors,
                                      node_edge_width=0.0,
                                      node_labels=node_labels,
                                      node_label_fontdict=node_label_fontdict,
                                      node_size=0.5,
                                      edge_color=mpl.rcParams['text.color'],
                                      # edge_labels=edge_labels,
                                      # edge_label_fontdict=edge_label_fontdict,
                                      edge_width=0.2,
                                      prettify=False)

        self.axes.set_xticks([])
        self.axes.set_yticks([])
        self.axes.set_aspect('equal')
        self.axes.figure.tight_layout()
        self.axes.figure.canvas.draw()


class BahnhofGraph:
    def __init__(self):
        self.canvas = FigureCanvas(Figure(figsize=(5, 3)))
        self.axes = self.canvas.figure.subplots()
        self.graph = None
        self.vereinfachter_graph = None

    def graph_vereinfachen(self, graph):
        g = graph.to_undirected()
        g = anlage.graph_weichen_ersetzen(g)
        g = anlage.graph_anschluesse_pruefen(g)
        g = anlage.graph_bahnsteigsignale_ersetzen(g)
        g = anlage.graph_signalpaare_ersetzen(g)
        g = anlage.graph_schleifen_aufloesen(g)
        g = anlage.graph_zwischensignale_entfernen(g)
        g = anlage.graph_schleifen_aufloesen(g)
        self.vereinfachter_graph = g

    def draw_graph(self, graph: nx.Graph, bahnsteig_graph: nx.Graph, filters: Optional[Iterable[Callable]] = None):
        self.axes.clear()

        if self.vereinfachter_graph is None:
            self.graph_vereinfachen(graph)

        colormap = {2: "tab:blue",  # Signal
                    3: "tab:gray",  # Weiche unten
                    4: "tab:gray",  # Weiche oben
                    5: "tab:red",  # Bahnsteig
                    6: "tab:pink",  # Einfahrt
                    7: "tab:purple",  # Ausfahrt
                    12: "tab:orange"}  # Haltepunkt

        node_colors = {key: colormap.get(typ, "r") for key, typ in self.vereinfachter_graph.nodes(data='typ', default='kein')}

        edge_labels = {(e1, e2): distanz
                       for e1, e2, distanz in self.vereinfachter_graph.edges(data='distanz', default=0)
                       if distanz > 0}
        edge_length = {(e1, e2): 0.01
                       for e1, e2, zeit in self.vereinfachter_graph.edges(data='distanz', default=0)}

        # node_size=3
        # node_edge_width
        node_label_fontdict = {"size": 10}
        edge_label_fontdict = {"size": 10, "bbox": {"boxstyle": "circle",
                                                    "fc": mpl.rcParams["axes.facecolor"],
                                                    "ec": mpl.rcParams["axes.facecolor"]}}
        self.graph = netgraph.InteractiveGraph(self.vereinfachter_graph, ax=self.axes,
                                      node_layout="geometric",
                                      node_layout_kwargs=dict(edge_length=edge_length),
                                      node_color=node_colors,
                                      node_edge_width=0.0,
                                      node_labels=True,
                                      node_label_fontdict=node_label_fontdict,
                                      node_size=1,
                                      edge_color=mpl.rcParams['text.color'],
                                      # edge_labels=edge_labels,
                                      # edge_label_fontdict=edge_label_fontdict,
                                      edge_width=0.2,
                                      prettify=False)

        self.axes.set_xticks([])
        self.axes.set_yticks([])
        self.axes.set_aspect('equal')
        self.axes.figure.tight_layout()
        self.axes.figure.canvas.draw()


def liniengraph_schleifen_aufloesen(g: nx.Graph):
    entfernen = set()

    for schleife in nx.simple_cycles(g):
        kanten = zip(schleife, schleife[1:] + schleife[:1])
        laengste_fahrzeit = 0
        summe_fahrzeit = 0
        laengste_kante = None
        for kante in kanten:
            fahrzeit = g.edges[kante].get("fahrzeit_min", 0)
            summe_fahrzeit += fahrzeit
            if fahrzeit > laengste_fahrzeit:
                laengste_fahrzeit = fahrzeit
                laengste_kante = kante

        if laengste_kante is not None:
            if laengste_fahrzeit > summe_fahrzeit - laengste_fahrzeit - len(schleife):
                entfernen.add(laengste_kante)
            else:
                print("symmetrische schleife", schleife)

    for u, v in entfernen:
        g.remove_edge(u, v)

    return g


class LinienGraph:
    def __init__(self):
        self.canvas = FigureCanvas(Figure(figsize=(5, 3)))
        self.axes = self.canvas.figure.subplots()
        self.graph = None

    def draw_graph(self, graph: nx.Graph, bahnsteig_graph: nx.Graph, filters: Optional[Iterable[Callable]] = None):
        self.axes.clear()

        if filters is None:
            filters = []
        for filt in filters:
            graph = filt(graph)

        colormap = {'bahnhof': 'tab:red', 'anschluss': 'tab:pink', 'H': 'tab:red', 'E': 'tab:pink', 'A': 'tab:pink'}
        node_colors = {key: colormap.get(typ, "r") for key, typ in graph.nodes(data='typ', default='kein')}

        edge_labels = {(e1, e2): str(round(zeit))
                       for e1, e2, zeit in graph.edges(data='fahrzeit_min', default=0)
                       if zeit > 0}
        edge_length = {(e1, e2): max(1/1000, zeit * 60 / 1000)
                       for e1, e2, zeit in graph.edges(data='fahrzeit_min', default=0)}

        # node_size=3
        # node_edge_width
        node_label_fontdict = {"size": 10}
        edge_label_fontdict = {"size": 10, "bbox": {"boxstyle": "circle",
                                                    "fc": mpl.rcParams["axes.facecolor"],
                                                    "ec": mpl.rcParams["axes.facecolor"]}}
        self.graph = netgraph.InteractiveGraph(graph,
                                      ax=self.axes,
                                      node_layout="geometric",
                                      node_layout_kwargs=dict(edge_length=edge_length),
                                      node_color=node_colors,
                                      node_edge_width=0.0,
                                      node_labels=True,
                                      node_label_fontdict=node_label_fontdict,
                                      node_size=1,
                                      edge_color=mpl.rcParams['text.color'],
                                      edge_labels=edge_labels,
                                      edge_label_fontdict=edge_label_fontdict,
                                      edge_width=0.2,
                                      # scale=(10., 10.),
                                      prettify=False)

        self.axes.set_xticks([])
        self.axes.set_yticks([])
        self.axes.set_aspect('equal')
        self.axes.figure.tight_layout()
        self.axes.figure.canvas.draw()


class GleisnetzWindow(QtWidgets.QMainWindow):

    def __init__(self, zentrale: DatenZentrale):
        super().__init__()

        self.zentrale = zentrale
        self.zentrale.anlage_update.register(self.anlage_update)

        self.ui = Ui_GleisnetzWindow()
        self.ui.setupUi(self)

        self.setWindowTitle("Netzplan")

        self.signal_graph = SignalGraph()
        self.signal_graph.canvas.setParent(self.ui.signal_graph_area)
        self.ui.signal_layout = QtWidgets.QHBoxLayout(self.ui.signal_graph_area)
        self.ui.signal_layout.setObjectName("signal_layout")
        self.ui.signal_layout.addWidget(self.signal_graph.canvas)
        self.signal_graph.canvas.setFocusPolicy(QtCore.Qt.ClickFocus)

        self.bahnhof_graph = BahnhofGraph()
        self.bahnhof_graph.canvas.setParent(self.ui.bahnhof_graph_area)
        self.ui.bahnhof_layout = QtWidgets.QHBoxLayout(self.ui.bahnhof_graph_area)
        self.ui.bahnhof_layout.setObjectName("bahnhof_layout")
        self.ui.bahnhof_layout.addWidget(self.bahnhof_graph.canvas)
        self.bahnhof_graph.canvas.setFocusPolicy(QtCore.Qt.ClickFocus)

        self.linien_graph = LinienGraph()
        self.linien_graph.canvas.setParent(self.ui.linien_graph_area)
        self.ui.linien_layout = QtWidgets.QHBoxLayout(self.ui.linien_graph_area)
        self.ui.linien_layout.setObjectName("linien_layout")
        self.ui.linien_layout.addWidget(self.linien_graph.canvas)
        self.linien_graph.canvas.setFocusPolicy(QtCore.Qt.ClickFocus)

        self.ui.signal_aktualisieren_button.clicked.connect(self.on_signal_aktualisieren_button_clicked)
        self.ui.linien_aktualisieren_button.clicked.connect(self.on_linie_aktualisieren_button_clicked)

        self.signal_graph.canvas.setFocus()

    @property
    def anlage(self) -> Anlage:
        return self.zentrale.anlage

    @property
    def client(self) -> PluginClient:
        return self.zentrale.client

    def anlage_update(self, *args, **kwargs):
        try:
            if self.client.signalgraph and not self.signal_graph.graph:
                self.signal_graph.draw_graph(self.client.signalgraph, self.client.bahnsteiggraph)

            if self.client.signalgraph and not self.bahnhof_graph.graph:
                self.bahnhof_graph.draw_graph(self.client.signalgraph, self.client.bahnsteiggraph)

            if self.client.liniengraph and not self.linien_graph.graph:
                self.linien_graph.draw_graph(self.client.liniengraph, self.client.bahnsteiggraph)
        except AttributeError as e:
            print(e)

    @pyqtSlot()
    def on_signal_aktualisieren_button_clicked(self):
        print("aktualisieren button clicked")
        filters = []

        if self.ui.signal_weichen_check.isChecked():
            filters.append(anlage.graph_weichen_ersetzen)
        if self.ui.signal_anschluss_check.isChecked():
            filters.append(anlage.graph_anschluesse_pruefen)
        if self.ui.signal_nachbarn_check.isChecked():
            filters.append(graph_nachbarbahnsteige_vereinen)
        if self.ui.signal_bahnsteig_check.isChecked():
            filters.append(anlage.graph_bahnsteigsignale_ersetzen)
        if self.ui.signal_paar_check.isChecked():
            filters.append(anlage.graph_signalpaare_ersetzen)
        if self.ui.signal_schleifen_check.isChecked():
            filters.append(anlage.graph_schleifen_aufloesen)
        if self.ui.signal_zwischen_check.isChecked():
            filters.append(anlage.graph_zwischensignale_entfernen)
        self.signal_graph.draw_graph(self.client.signalgraph, self.client.bahnsteiggraph, filters=filters)

        print("exit clicked handler")

    @pyqtSlot()
    def on_linie_aktualisieren_button_clicked(self):
        print("aktualisieren button clicked")
        filters = []

        if self.ui.linien_schleifen_check.isChecked():
            filters.append(liniengraph_schleifen_aufloesen)
        self.linien_graph.draw_graph(self.client.liniengraph, self.client.bahnsteiggraph, filters=filters)

        print("exit clicked handler")
