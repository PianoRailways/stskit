import trio
import logging
from typing import Any, Callable, Dict, Iterable, Optional, Set, Tuple, Union

import networkx as nx

from stskit.interface.stsobj import time_to_minutes, time_to_seconds, minutes_to_time, seconds_to_time
from stskit.interface.stsplugin import PluginClient, TaskDone


logger = logging.getLogger(__name__)
logger.addHandler(logging.NullHandler())


class GraphClient(PluginClient):
    """
    Erweiterter PluginClient mit Graphdarstellung der Basisdaten vom Simulator.

    Die Klasse unterhält folgende Graphen:

    Signalgraph
    ===========

    Der _Signalgraph_ enthält das Gleisbild aus der Wegeliste der Plugin-Schnittstelle mit sämtlichen Knoten und Kanten.
    Das 'typ'-Attribut wird auf den sts-Knotentyp (int) gesetzt.
    Kanten werden entsprechend der Nachbarrelationen aus der Wegeliste ('typ'-attribut 'gleis') gesetzt.
    Der Graph ist gerichtet, da die nachbarbeziehung i.a. nicht reziprok ist.
    Die Kante zeigt auf die Knoten, die als Nachbarn aufgeführt sind.
    Meist werden von der Schnittstelle jedoch Kanten in beide Richtungen angegeben,
    weshalb z.B. nicht herausgefunden werden kann, für welche Richtung ein Signal gilt.

    Der Graph sollte nicht verändert werden.
    Es wird nicht erwartet, dass sich der Graph im Laufe eines Spiels ändert.

    Signal-Attribute
    ----------------

    Knoten 'typ': (int) stsobj.Knoten.TYP_NUMMER
    Kanten 'typ': (str) 'gleis'
    Kanten 'distanz': (int) Länge (Anzahl Knoten) des kürzesten Pfades zwischen den Knoten. Wird auf 1 gesetzt.

    Bahnsteiggraph
    ==============

    Der _Bahnsteiggraph_ enthält alle Bahnsteige aus der Bahnsteigliste der Plugin-Schnittstelle als Knoten.
    Kanten werden entsprechend der Nachbarrelationen gesetzt.
    Der Graph ist ungerichtet, da die Nachbarbeziehung als reziprok aufgefasst wird.

    Der Graph sollte nicht verändert werden.
    Es wird nicht erwartet, dass sich der Graph im Laufe eines Spiels ändert.

    Bahnsteig-Attribute
    -------------------

    Kanten 'typ': (str) 'bahnsteig'.
    Kanten 'distanz': (int) Länge (Anzahl Knoten) des kürzesten Pfades zwischen den Knoten. Wird auf 0 gesetzt.


    Zuggraph
    ========

    Der _Zuggraph_ enthält alle Züge aus der Zugliste der Plugin-Schnittstelle als Knoten.
    Kanten werden aus den Ersatz-, Kuppeln- und Flügeln-Flags gebildet.

    Der Zuggraph verändert sich im Laufe eines Spiels.
    Neue Züge werden hinzugefügt.

    In der aktuellen Entwicklerversion werden ausgefahrene Züge beibehalten.
    Falls sich das als nicht praktikabel erweist, werden die Züge wie in der Zugliste gelöscht.

    Knotenattribute
    ---------------

    obj (stsobj.ZugDetails): Zugobjekt
    zid (int): Zug-ID

    Kantenattribute
    ---------------

    typ (str): Verbindungstyp
        'P': planmässige Fahrt
        'E': Ersatzzug
        'F': Flügelung
        'K': Kupplung


    Zielgraph
    =========

    Der Zielgraph enthält die Zielpunkte aller Züge.
    Die Punkte sind gemäss Anordnung im Fahrplan
    sowie planmässigen Abhängigkeiten (Ersatz, Kuppeln, Flügeln) verbunden.

    Knotenattribute
    ---------------

    obj (stsobj.FahrplanZeile): Fahrplanziel-Objekt
    fid (Tupel): Fahrplanziel-ID, siehe stsobj.FahrplanZeile.fid Property.
    plan (str): Plangleis
    typ (str): Zielpunkttyp:
        'H': Planmässiger Halt
        'D': Durchfahrt
    an (int/float): planmässige Ankunftszeit in Minuten
    ab (int/float): planmässige Abfahrtszeit in Minuten


    Kantenattribute
    ---------------

    typ (str): Verbindungstyp
        'P': planmässige Fahrt
        'E': Ersatzzug
        'F': Fluegelung
        'K': Kupplung

    """

    def __init__(self, name: str, autor: str, version: str, text: str):
        super().__init__(name, autor, version, text)

        self.signalgraph = nx.DiGraph()
        self.bahnsteiggraph = nx.Graph()
        self.zuggraph = nx.DiGraph()
        self.zielgraph = nx.DiGraph()

    async def request_bahnsteigliste(self):
        await super().request_bahnsteigliste()
        self._bahnsteig_graph_erstellen()

    async def request_wege(self):
        await super().request_wege()
        self._signalgraph_erstellen()

    async def request_zugliste(self):
        await super().request_zugliste()
        self._zuggraph_erstellen()

    async def request_zugdetails_einzeln(self, zid: int):
        result = await super().request_zugdetails_einzeln(zid)
        if result:
            self._zuggraph_update_zug(zid)
        return result

    async def request_zugfahrplan_einzeln(self, zid: int) -> bool:
        result = await super().request_zugfahrplan_einzeln(zid)
        if result:
            self._zielgraph_update_zug(zid)
        return result

    def _signalgraph_erstellen(self):
        """
        Signalgraph erstellen.

        Die Graphen werden in der Dokumentation der Klasse beschrieben.

        :return: None
        """

        self.signalgraph.clear()

        for knoten1 in self.wege.values():
            if knoten1.name:
                self.signalgraph.add_node(knoten1.name, typ=knoten1.typ)
                for knoten2 in knoten1.nachbarn:
                    if knoten2.name:
                        self.signalgraph.add_edge(knoten1.name, knoten2.name, typ='gleis', distanz=1)

    def _bahnsteig_graph_erstellen(self):
        """
        Bahnsteiggraph erstellen.

        Die Graphen werden in der Dokumentation der Klasse beschrieben.

        :return: None
        """

        self.bahnsteiggraph.clear()

        for bs1 in self.bahnsteigliste.values():
            for bs2 in bs1.nachbarn:
                self.bahnsteiggraph.add_edge(bs1.name, bs2.name, typ='bahnsteig', distanz=0)

    def _zuggraph_erstellen(self, clean=False):
        """
        Zuggraph erstellen bzw. aktualisieren.

        Die Graphen werden in der Dokumentation der Klasse beschrieben.

        Per Voreinstellung (clean=False),
        fügt diese Methode neue Knoten und ihre Kanten zum Graphen hinzu.
        Bestehende Knoten werden nicht verändert.
        Um den Graphen neu aufzubauen, sollte clean=True übergeben werden.

        :return: None
        """

        if clean:
            self.zuggraph.clear()

        for zid in self.zugliste:
            self._zuggraph_update_zug(zid)

    def _zuggraph_update_zug(self, zid: int):
        """
        Einzelnen Zug im Zuggraph aktualisieren.

        Wenn der Zugknoten existiert wird er aktualisiert, sonst neu erstellt.
        """

        zug = self.zugliste[zid]

        zug_data = {'zid': zid,
                    'obj': zug,
                    'name': zug.name}
        self.zuggraph.add_node(zid, **zug_data)

    def _zielgraph_erstellen(self, clean=False):
        """
        Ziel- und Zuggraphen erstellen bzw. aktualisieren.

        Die Graphen werden in der Dokumentation der Klasse beschrieben.

        Per Voreinstellung (clean=False),
        fügt diese Methode neue Knoten und ihre Kanten zum Graphen hinzu.
        Bestehende Knoten werden nicht verändert.
        Um den Graphen neu aufzubauen, sollte clean=True übergeben werden.

        :return: None
        """

        if clean:
            self.zuggraph.clear()
            self.zielgraph.clear()

        for zid2, zug2 in self.zugliste.items():
            self._zielgraph_update_zug(zid2)

    def _zielgraph_update_zug(self, zid: int):
        """
        Ziel- und Zuggraphen nach Fahrplan eines Zuges aktualisieren.

        Die Graphen werden in der Dokumentation der Klasse beschrieben.

        Diese Methode fügt neue Knoten und ihre Kanten zum Graphen hinzu oder aktualisiert bestehende.
        Es werden keine Knoten und Kanten gelöscht.

        Bemerkungen
        -----------

        - Der vom Simulator gemeldete Fahrplan enthält nur anzufahrende Ziele.
          Im Zielgraphen werden die abgefahrenen Ziele jedoch beibehalten.

        :param: zid: Zug-ID. Der Zug muss in der Zugliste enthalten sein.
        :return: None
        """

        ziel1 = None
        fid1 = None
        zid2 = zid
        zug2 = self.zugliste[zid]

        self._zuggraph_update_zug(zid)

        for ziel2 in zug2.fahrplan:
            fid2 = ziel2.fid
            ziel_data = {'fid': fid2,
                         'obj': ziel2,
                         'plan': ziel2.plan,
                         'typ': 'D' if ziel2.durchfahrt() else 'H'}
            if ziel2.an is not None:
                ziel_data['an'] = time_to_minutes(ziel2.an)
            if ziel2.ab is not None:
                ziel_data['ab'] = time_to_minutes(ziel2.ab)
            self.zielgraph.add_node(fid2, **ziel_data)

            if ziel1:
                if fid1 != fid2:
                    self.zielgraph.add_edge(fid1, fid2, typ='P')

            if zid3 := ziel2.ersatz_zid():
                self._zielgraph_link_flag(ziel2, zid3, 'E')

            if zid3 := ziel2.kuppel_zid():
                self._zielgraph_link_flag(ziel2, zid3, 'K')

            if zid3 := ziel2.fluegel_zid():
                self._zielgraph_link_flag(ziel2, zid3, 'F')

            ziel1 = ziel2
            fid1 = fid2

    def _zielgraph_link_flag(self, ziel2, zid3, typ):
        """
        Zugziele verknüpfen.

        Unterfunktion von _zielgraph_update_zug.
        """

        fid2 = ziel2.fid
        zid2 = ziel2.zug.zid

        if zid2 != zid3:
            self.zuggraph.add_edge(zid2, zid3, typ=typ)
        try:
            zug3 = self.zugliste[zid3]
            if typ == 'K':
                _, ziel3 = zug3.find_fahrplan(plan=ziel2.plan, zeit=ziel2.an)
            else:
                ziel3 = zug3.fahrplan[0]
            fid3 = ziel3.fid
        except (AttributeError, IndexError, KeyError):
            logger.debug(f"{typ}-Ziel von {fid2} oder Zug {zid3} nicht gefunden")
        else:
            if fid2 != fid3:
                self.zielgraph.add_edge(fid2, fid3, typ=typ)


async def test() -> GraphClient:
    """
    Testprogramm

    Das testprogramm fragt alle Daten einmalig vom Simulator ab.

    Der GraphClient bleibt bestehen, damit weitere Details aus den statischen Attributen ausgelesen werden können.
    Die Kommunikation mit dem Simulator wird jedoch geschlossen.

    :return: GraphClient-instanz
    """

    client = GraphClient(name='test', autor='tester', version='0.0', text='testing the graph client')
    await client.connect()

    try:
        async with client._stream:
            async with trio.open_nursery() as nursery:
                await nursery.start(client.receiver)
                await client.register()
                await client.request_simzeit()
                await client.request_anlageninfo()
                await client.request_bahnsteigliste()
                await client.request_wege()
                await client.request_zugliste()
                await client.request_zugdetails()
                await client.request_zugfahrplan()
                await client.resolve_zugflags()
                raise TaskDone()
    except TaskDone:
        pass

    return client


if __name__ == '__main__':
    trio.run(test)
