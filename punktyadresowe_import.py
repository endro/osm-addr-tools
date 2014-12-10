#!/usr/bin/env python3.4
# # -*- coding: UTF-8 -*-
#
# punktyadresowe_import.py CC-BY-NC-SA 3.0 WiktorN
#
# Based on work by Grzegorz Sapijaszko (http://wiki.openstreetmap.org/wiki/User:Gsapijaszko/punktyadresowe_import)
#
# dependencies:
# Beautiful-Soup (http://www.crummy.com/software/BeautifulSoup/)
#       pip install beautifulsoup4
#       easy_install beautifulsoup4
#       apt-get install python-beautifulsoup4
#       portmaster www/py-beautifulsoup
#
# TODO:
# - extract validations (mixed addressing scheme) to external module

import sys
if sys.version_info.major == 2:
    from urllib import urlencode
    from urllib2 import urlparse, urlopen
    import urllib2 as urequest
    str_normalize = lambda x: x.decode('utf-8')
else:
    from urllib.parse import urlencode, urlparse
    import urllib.request as urequest
    from urllib.request import urlopen
    str_normalize = lambda x: x

import argparse
from bs4 import BeautifulSoup
from collections import namedtuple
from functools import partial
import json
import logging
import math
import pyproj
import re
from shapely.geometry import Point

from osmdb import OsmDb
import overpass
from mapping import mapstreet, mapcity
from utils import parallel_execution, groupby
import lxml.html
import lxml.etree


# stałe
#_EPSG2180 = Proj(init='epsg:2180')

__log = logging.getLogger(__name__)
# User-Agent dla requestów
__opener = urequest.build_opener()
__headers = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 5.1; rv:10.0.2) Gecko/20100101 Firefox/10.0.2',
}
__opener.addheaders = __headers.items()


# setup
urequest.install_opener(__opener)

__WGS84 = pyproj.Proj(proj='latlong',datum='WGS84')
__EPSG2180 = pyproj.Proj(init="epsg:2180")

def wgsTo2180(lon, lat):
    # returns lon,lat
    return pyproj.transform(__WGS84, __EPSG2180, lon, lat)

def e2180toWGS(lon, lat):
    # returns lon,lat
    return pyproj.transform(__EPSG2180, __WGS84, lon, lat)

def _filterOnes(lst):
    return list(filter(lambda x: x > 0, lst))


def convertToOSM(lst):
    ret = """<?xml version='1.0' encoding='UTF-8'?>
<osm version='0.6' upload='false' generator='punktyadresowe_import.php'>
"""
    ret = BeautifulSoup("", "xml")
    osm = ret.new_tag('osm', version='0.6', upload='false', generator='punktyadresowe_import.py')
    ret.append(osm)

    for (node_id, val) in enumerate(lst):
        osm.append(val.asOsmSoup(-1 * (node_id + 1)))

    return ret.prettify()

class Address(object): #namedtuple('BaseAddress', ['housenumber', 'postcode', 'street', 'city', 'sym_ul', 'simc', 'source', 'location'])):
    def __init__(self, housenumber='', postcode='', street='', city='', sym_ul='', simc='', source='', location=''):
        #super(Address, self).__init__(*args, **kwargs)
        self.housenumber = housenumber
        if postcode and postcode != '00-000' and re.match('^[0-9]{2}-[0-9]{3}$', postcode):
            self.postcode = postcode
        else:
            self.postcode = ''
        if street:
            self.street = mapstreet(street.replace('  ', ' '), sym_ul)
        else:
            self.street = ''
        self.city = mapcity(city, simc)
        if sym_ul: # add sanity cheks
            self.sym_ul = sym_ul
        else:
            self.sym_ul = ''
        if simc:
            self.simc = simc #add sanity checks
        else:
            self.simc = ''
        self.source = source
        self.location = location
        self._fixme = []
        assert all(map(lambda x: isinstance(getattr(self, x, ''), str), ('housenumber', 'postcode', 'street', 'city', 'sym_ul', 'simc', 'source')))
        assert isinstance(self.location, dict)
        assert 'lon' in self.location
        assert 'lat' in self.location

    def addFixme(self, value):
        self._fixme.append(value)

    def getFixme(self):
        return ",".join(self._fixme)

    def asOsmSoup(self, node_id):
        ret = BeautifulSoup("", "xml")
        node = ret.new_tag('node', id=node_id, action='modify', visible='true', lat=self.location['lat'], lon=self.location['lon'])
        node.append(ret.new_tag('tag', k='addr:housenumber', v=self.housenumber))
        if self.postcode:
            node.append(ret.new_tag('tag', k='addr:postcode', v=self.postcode))
        if self.street:
            node.append(ret.new_tag('tag', k='addr:street', v=self.street))
            node.append(ret.new_tag('tag', k='addr:city', v=self.city))
        else:
            node.append(ret.new_tag('tag', k='addr:place', v=self.city))

        node.append(ret.new_tag('tag', k='addr:simc', v=self.simc))
        node.append(ret.new_tag('tag', k='source:addr', v=self.source))
        if self._fixme:
            node.append(ret.new_tag('tag', k='fixme', v=" ".join(self.fixme)))
        return node

    def osOsmXML(self, node_id):
        return asOsmSoup.prettify()

    def getLatLon(self):
        return tuple(map(float, (self.location['lat'], self.location['lon'])))

    def get_point(self):
        return Point(reversed(self.getLatLon()))

    @property
    def center(self):
        return self.get_point()

    def similar_to(self, other):
        ret = True
        ret &= (other.housenumber.upper().replace(' ', '') == self.housenumber.upper().replace(' ', ''))
        if self.simc and other.simc and self.simc == other.simc:
            ret &= True
        else:
            ret &= (other.city == self.city)
        if self.sym_ul and other.sym_ul:
            ret &= (self.sym_ul == other.sym_ul)
            # skip comparing street names, might be a bit different
        return ret

    def __str__(self):
        if self.street:
            return "%s, %s, %s" % (self.city, self.street, self.housenumber)
        return "%s, %s" % (self.city, self.housenumber)

    def __repr__(self):
         return type(self).__name__ + ", ".join(
                                      "%s=%s" % (x, getattr(self, x)) for x in (
                                                        'housenumber', 'postcode',
                                                        'street', 'city', 'sym_ul',
                                                        'simc', 'source', 'location')
         )

    def get_index_key(self):
        return tuple(map(str.upper, (self.city.strip(), self.street.strip(), self.housenumber.replace(' ', ''))))

    def to_JSON(self):
        return {
            'addr:housenumber': self.housenumber,
            'addr:postcode': self.postcode,
            'addr:street': self.street,
            'addr:city': self.city,
            'teryt:sym_ul': self.sym_ul,
            'teryt:simc': self.simc,
            'source:addr': self.source,
            'location': self.location,
            'fixme': ",".join(self._fixme),
        }

    @staticmethod
    def from_JSON(obj):
        ret = Address(
            housenumber = obj['addr:housenumber'],
            postcode    = obj.get('addr:postcode'),
            street      = obj.get('addr:street'),
            city        = obj.get('addr:city'),
            sym_ul      = obj.get('teryt:symul'),
            simc        = obj.get('teryt:simc'),
            source      = obj['source:addr'],
            location    = obj['location'])
        if obj.get('fixme'):
            ret.addFixme(obj['fixme'])
        return ret

class AbstractImport(object):
    __log = logging.getLogger(__name__).getChild('AbstractImport')

    def __init__(self, terc, *args, **kwargs):
        if terc:
            query = """
[out:xml];
relation
    ["teryt:terc"="%s"]
    ["boundary"="administrative"]
    ["admin_level"="7"];
out bb;
>;
out bb;
            """ % (terc,)
            data = BeautifulSoup(overpass.query(query))
            ret = data.osm.relation.bounds
            self.bbox = (
                ret['minlon'],
                ret['minlat'],
                ret['maxlon'],
                ret['maxlat'],
            )
            osmdb = OsmDb(data)
            self.shape = osmdb.get_shape(data.osm.relation)

    def getBbox():
        """
        this functions returns bbox of imported area using WGS84 lonlat as tuple:
        (minlon, minlat, maxlon, maxlat)
        """
        return self.bbox

    def getBbox2180(self):
        return wgsTo2180(*self.bbox[:2]) + wgsTo2180(*self.bbox[2:])

    def setBboxFrom2180(self, bbox):
        self.bbox = e2180toWGS(*bbox[:2]) + e2180toWGS(*bbox[2:])


    def fetchTiles(self):
        """
        this function returns list of Address'es of imported area
        """
        raise NotImplementedError("")

    def _checkDuplicatesInImport(self, data):
        addr_index = groupby(data, lambda x: (x.city, x.housenumber, x.street))

        for (addr, occurances) in filter(lambda x: len(x[1]) > 1, addr_index.items()):
            self.__log.warning("Duplicte addresses in import: %s", addr)
            for i in occurances:
                i.addFixme('Duplicate address in import')


    def _checkMixedScheme(self, data):
        dups = groupby(data, lambda x: x.simc, lambda x: bool(x.street))

        dups_count = dict((k, len(_filterOnes(v))) for k, v in dups.items())
        dups = dict((k, len(_filterOnes(v))/len(v)) for k, v in dups.items())
        dups = dict((k,v) for k, v in filter(lambda x: 0 < x[1] and x[1] < 1, dups.items()))

        for i in filter(
                lambda x: not bool(x.street),
                filter(
                    lambda x: x.simc in dups.keys(),
                    data
                    )
                ):
            i.addFixme('Mixed addressing scheme in city - with streets and without. %.1f%% (%d) with streets.' % (dups[i.simc]*100, dups_count[i.simc]))

    def getAddresses(self):
        data = self.fetchTiles()
        self._checkDuplicatesInImport(data)
        self._checkMixedScheme(data)
        return data


class iMPA(AbstractImport):
    __log = logging.getLogger(__name__).getChild('iMPA')

    def __init__(self, gmina=None, wms=None, terc=None):
        self.wms = None

        if gmina:
            self._initFromIMPA('http://%s.e-mapa.net' % (gmina,))

        else:
            if not wms and not terc:
                raise ValueError("If no gmina provided then wms and terc are required")
            super(iMPA, self).__init__(terc=terc)

        if wms:
            self.wms = wms

        if not self.wms:
            raise ValueError("No WMS address found")

    def _initFromIMPA(self, gmina_url):
        url = gmina_url + '/application/system/init.php'
        self.__log.info(url)
        data = urlopen(url).read().decode('utf-8')
        init_data = json.loads(data)

        self.setBboxFrom2180(init_data['spatialExtent'])
        self.terc = init_data['teryt']

        address_layers = list(
                    filter(
                        lambda x: x['title'] and x['title'].upper() == 'ADRESY I ULICE',
                        init_data['map']['services']
                    )
            )
        if len(address_layers) == 0:
            self.__log.warning('No information about address layer in init.php')
            self.__log.debug(data)
        else:
            self.wms = address_layers[0]['address']

    def fetchPoint(self, wms_addr, w, s, e, n, pointx, pointy, layer="punkty"):
        params = {
            'VERSION': '1.1.1',
            'SERVICE': 'WMS',
            'REQUEST': 'GetFeatureInfo',
            'LAYERS': layer, # było: ulice,punkty
            'QUERY_LAYERS': layer, # było: ulice, punkty
            'FORMAT': 'image/png',
            'INFO_FORMAT': 'text/html',
            'SRS': 'EPSG:2180',
            'FEATURE_COUNT': '10000000', # wystarczająco dużo, by ogarnąć każdą sytuację
            'WIDTH': 2,
            'HEIGHT': 2,
            'BBOX': '%s,%s,%s,%s' % (w, s, e, n),
            'X': pointx,
            'Y': pointy,
        }

        josm_wms = {
            'VERSION': '1.1.1',
            'SERVICE': 'WMS',
            'REQUEST': 'GetMap',
            'LAYERS': layer,
            'FORMAT': 'image/png',
            'TRANSPARENT': 'true',
        }

        #TODO: do proper URL parsing
        if '?' in wms_addr:
            url = "%s&%s" % (wms_addr, urlencode(params))
            self.__log.warning("JOSM layer: %s&%s&SRS={proj}&WIDTH={width}&HEIGHT={height}&BBOX={bbox}" % (wms_addr, urlencode(josm_wms)))
        else:
            url = "%s?%s" % (wms_addr, urlencode(params))
            self.__log.warning("JOSM layer: %s?%s&SRS={proj}&WIDTH={width}&HEIGHT={height}&BBOX={bbox}" % (wms_addr, urlencode(josm_wms)))
        self.__log.info(url)
        data = urlopen(url).read()
        return data

    def _convertToAddress(self, soup):
        kv = dict(zip(
            map(lambda x: str(x.text), soup.find_all('th')),
            map(lambda x: str(x.text), soup.find_all('td'))
        ))
        try:
            (lon, lat) = map(lambda x: x[2:], kv[str_normalize('GPS (WGS 84)')].split(', ', 1))
            (str_name, str_id) = kv[str_normalize('Nazwa ulicy(Id GUS)')].rsplit('(', 1)
            (city_name, city_id) = kv[str_normalize('Miejscowość(Id GUS)')].rsplit('(', 1)

            if float(lon) < 14 or float(lon) > 25 or float(lat) < 49 or float(lat) > 56:
                self.__log.warning("Point out of Polish borders: (%s, %s), %s, %s, %s", lat, lon, city_name, str_name, kv[str_normalize('Numer')])

            return Address(
                kv[str_normalize('Numer')],
                kv[str_normalize('Kod pocztowy')].strip(),
                str_name.strip(),
                city_name.strip(),
                str_id[:-1], # sym_ul
                city_id[:-1], # simc
                kv[str_normalize('Źródło danych')],
                {'lat': lat, 'lon': lon} # location
            )
        except KeyError:
            self.__log.error(soup)
            self.__log.error(kv)
            self.__log.error("Exception during point analysis", exc_info=True)
            raise
        except ValueError:
            self.__log.error(soup)
            self.__log.error(kv)
            self.__log.error("Exception during point analysis", exc_info=True)
            raise

    def fetchTiles(self):
        html = self.fetchPoint(
            self.wms,
            *self.getBbox2180(),
            pointx=0, pointy=0 # sprawdź punkt (0,0) i tak powinno zostać zwrócone wszystko
        )
        ret = list(map(self._convertToAddress, BeautifulSoup(html).find_all('table')))
        return ret

class GUGiK(AbstractImport):
    # parametry do EPSG 2180
    __MAX_BBOX_X = 20000
    __MAX_BBOX_Y = 45000
    __PRECISION = 10
    __base_url = "http://emuia.gugik.gov.pl/wmsproxy/emuia/wms?SERVICE=WMS&FORMAT=application/vnd.google-earth.kml+xml&VERSION=1.1.1&SERVICE=WMS&REQUEST=GetMap&LAYERS=emuia:layer_adresy_labels&STYLES=&SRS=EPSG:2180&WIDTH=16000&HEIGHT=16000&BBOX="
    __log = logging.getLogger(__name__).getChild('GUGiK')

    def __init__(self, terc):
        super(GUGiK, self).__init__(terc=terc)
        self.terc = terc

    @staticmethod
    def divideBbox(minx, miny, maxx, maxy):
        """divides bbox to tiles of maximum supported size by EUiA WMS"""
        return [
            (x / GUGiK.__PRECISION,
             y / GUGiK.__PRECISION,
            min(x / GUGiK.__PRECISION + GUGiK.__MAX_BBOX_X, maxx),
            min(y / GUGiK.__PRECISION + GUGiK.__MAX_BBOX_Y, maxy))
            for x in range(math.floor(minx * GUGiK.__PRECISION), math.ceil(maxx * GUGiK.__PRECISION), GUGiK.__MAX_BBOX_X * GUGiK.__PRECISION)
            for y in range(math.floor(miny * GUGiK.__PRECISION), math.ceil(maxy * GUGiK.__PRECISION), GUGiK.__MAX_BBOX_Y * GUGiK.__PRECISION)
        ]


    def _convertToAddress(self, soup):
        desc_soup = lxml.html.fromstring(str(soup.description.string))
        addr_kv = dict(
            (
             str(x.find('strong').find('span').text),
             str(x.find('span').text)
            ) for x in desc_soup.find('ul').iterchildren()
        )

        coords = soup.Point.coordinates.string.split(',')
        ret = Address(
                addr_kv[str_normalize('NUMER_PORZADKOWY')],
                addr_kv.get(str_normalize('KOD_POCZTOWY')),
                addr_kv.get(str_normalize('NAZWA_ULICY')),
                addr_kv[str_normalize('NAZWA_MIEJSCOWOSCI')],
                addr_kv.get(str_normalize('TERYT_ULICY')),
                addr_kv[str_normalize('TERYT_MIEJSCOWOSCI')],
                'emuia.gugik.gov.pl',
                {'lat': coords[1], 'lon': coords[0]}
        )
        ret.status = addr_kv[str_normalize('STATUS')]
        ret.wazny_do = addr_kv.get(str_normalize('WAZNY_DO'))
        return ret

    def _isEligible(self, addr):
        # TODO: check status?
        if addr.status.upper() != 'ZATWIERDZONY':
            self.__log.info('Ignoring address %s, because status %s is not ZATWIERDZONY', addr, addr.status.upper())
            return False
        if addr.wazny_do:
            self.__log.info('Ignoring address %s, because it has set WAZNY_DO=%s', addr, addr.wazny_do)
            return False
        if '?' in addr.housenumber or 'bl' in addr.housenumber:
            self.__log.info('Ignoring address %s because has strange housenumber: %s', addr, addr.housenumber)
            return False
        if not addr.get_point().within(self.shape):
            # do not report anything about this, this is normal
            return False
        return True

    def fetchTiles(self):
        bbox = self.getBbox2180()
        ret = []
        for i in self.divideBbox(*bbox):
            url = GUGiK.__base_url+",".join(map(str, i))
            self.__log.info("Fetching from EMUIA: %s", url)
            soup = lxml.etree.fromstring(urlopen(url).read())
            doc = soup.find('{http://www.opengis.net/kml/2.2}Document') # be namespace aware
            if doc:
                ret.extend(filter(
                    self._isEligible,
                    map(self._convertToAddress, doc.iterchildren('{http://www.opengis.net/kml/2.2}Placemark'))
                    )
                )
            else:
                raise ValueError('No data returned from GUGiK possibly to wrong scale. Check __MAX_BBOX_X, __MAX_BBOX_Y, HEIGHT and WIDTH')
        return ret

class AddressEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, Address):
            return obj.to_JSON()
        return json.JSONEncoder.default(self, obj)

def main():
    parser = argparse.ArgumentParser(description="Downloads data from iMPA and saves in OSM or JSON format. CC-BY-SA 3.0 @ WiktorN. Filename is <gmina>.osm or <gmina>.json")
    parser.add_argument('--output-format', choices=['json', 'osm'],  help='output file format - "json" or "osm", default: osm', default="osm", dest='output_format')
    parser.add_argument('--source', choices=['impa', 'gugik'],  help='input source: "gugik" or "impa". Emuia requires providing teryt:terc code. Defaults to "impa"', default="impa", dest='source')
    parser.add_argument('--log-level', help='Set logging level (debug=10, info=20, warning=30, error=40, critical=50), default: 20', dest='log_level', default=20, type=int)
    parser.add_argument('--no-mapping', help='Disable mapping of streets and cities', dest='no_mapping', default=False, action='store_const', const=True)
    parser.add_argument('--wms', help='Override WMS address with address points', dest='wms', default=None)
    parser.add_argument('--terc', help='teryt:terc code which defines area of operation', dest='terc', default=None)
    parser.add_argument('gmina', nargs='*',  help='list of iMPA services to download, it will use at most 4 concurrent threads to download and analyse')
    args = parser.parse_args()

    logging.basicConfig(level=args.log_level)

    if args.no_mapping:
        global mapstreet, mapcity
        mapstreet = lambda x, y: x
        mapcity = lambda x, y: x
    if args.source == "impa":
        imp_gen = partial(iMPA, wms=args.wms, terc=args.terc)
    else:
        imp_gen = partial(GUGiK, terc=args.terc)
    if args.gmina:
        rets = parallel_execution(*map(lambda x: lambda: imp_gen(x).getAddresses(), args.gmina))
        #rets = list(map(lambda x: impa_gen(x).fetchTiles(), args.gmina)) # usefull for debugging
    else:
        rets = [imp_gen().getAddresses(),]
    if args.output_format == 'json':
        write_conv_func = lambda x: json.dumps(list(x), cls=AddressEncoder)
        file_suffix = '.json'
    else:
        write_conv_func = convertToOSM
        file_suffix = '.osm'

    if args.gmina:
        for (ret, gmina) in zip(rets, args.gmina):
            with open(gmina+file_suffix, "w+", encoding='utf-8') as f:
                f.write(write_conv_func(ret))
    else:
        with open('result.osm', 'w+', encoding='utf-8') as f:
            f.write(write_conv_func(rets[0]))

if __name__ == '__main__':
    main()
