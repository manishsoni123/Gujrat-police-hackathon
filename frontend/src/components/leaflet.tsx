/**
 * Minimal React bindings for Leaflet (BSD-2-Clause) written for Sentinel Gujarat
 * so the runtime bundle carries no react-leaflet (Hippocratic License 2.1,
 * not OSI-approved). Only the surface the pages use is implemented:
 * MapContainer, TileLayer, Marker (+ Popup child), Polyline, GeoJSON,
 * useMap and useMapEvents. Every component renders nothing itself; the
 * Leaflet layer is created on mount, updated on prop change and removed on
 * unmount, exactly as react-leaflet did for these call sites.
 */
import { createContext, useContext, useEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createPortal } from 'react-dom';
import L from 'leaflet';

const MapContext = createContext<L.Map | null>(null);
const MarkerContext = createContext<L.Marker | null>(null);

/** The Leaflet map of the nearest <MapContainer>. Throws outside one. */
export function useMap(): L.Map {
  const map = useContext(MapContext);
  if (!map) throw new Error('useMap() must be used inside <MapContainer>');
  return map;
}

/** Subscribes the given handlers to map events for the component's lifetime (latest handlers always used). */
export function useMapEvents(handlers: L.LeafletEventHandlerFnMap): L.Map {
  const map = useMap();
  const ref = useRef(handlers);
  ref.current = handlers;
  const names = Object.keys(handlers).sort().join(',');
  useEffect(() => {
    const bound: Record<string, L.LeafletEventHandlerFn> = {};
    names
      .split(',')
      .filter(Boolean)
      .forEach((name) => {
        bound[name] = (e: L.LeafletEvent) => {
          const fn = ref.current[name as keyof L.LeafletEventHandlerFnMap] as ((ev: L.LeafletEvent) => void) | undefined;
          fn?.(e);
        };
        map.on(name, bound[name]);
      });
    return () => {
      Object.entries(bound).forEach(([name, fn]) => map.off(name, fn));
    };
  }, [map, names]);
  return map;
}

interface MapContainerProps {
  center: [number, number];
  zoom: number;
  style?: CSSProperties;
  className?: string;
  scrollWheelZoom?: boolean;
  preferCanvas?: boolean;
  children?: ReactNode;
}

/** Creates the L.Map once; `center`/`zoom` are initial values (callers use FitBounds / setView for later moves). */
export function MapContainer({ center, zoom, style, className, scrollWheelZoom = true, preferCanvas = false, children }: MapContainerProps) {
  const ref = useRef<HTMLDivElement>(null);
  const [map, setMap] = useState<L.Map | null>(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    const m = L.map(el, { center, zoom, scrollWheelZoom, preferCanvas });
    setMap(m);
    return () => {
      setMap(null);
      m.remove();
    };
    // The map is created once per mount; later prop changes are intentionally ignored (react-leaflet parity).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  return (
    <div ref={ref} style={style} className={className}>
      {map ? <MapContext.Provider value={map}>{children}</MapContext.Provider> : null}
    </div>
  );
}

interface TileLayerProps {
  url: string;
  attribution?: string;
  maxZoom?: number;
}

export function TileLayer({ url, attribution, maxZoom }: TileLayerProps) {
  const map = useMap();
  useEffect(() => {
    const layer = L.tileLayer(url, { attribution, maxZoom }).addTo(map);
    return () => {
      layer.remove();
    };
  }, [map, url, attribution, maxZoom]);
  return null;
}

interface MarkerProps {
  position: [number, number];
  icon?: L.Icon | L.DivIcon;
  title?: string;
  eventHandlers?: L.LeafletEventHandlerFnMap;
  children?: ReactNode;
}

export function Marker({ position, icon, title, eventHandlers, children }: MarkerProps) {
  const map = useMap();
  const [marker, setMarker] = useState<L.Marker | null>(null);
  const [lat, lng] = position;

  useEffect(() => {
    const m = L.marker([lat, lng], { icon, title }).addTo(map);
    setMarker(m);
    return () => {
      setMarker(null);
      m.remove();
    };
    // Created once per mount; position/icon updates are applied by the effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);

  useEffect(() => {
    marker?.setLatLng([lat, lng]);
  }, [marker, lat, lng]);

  useEffect(() => {
    if (marker && icon) marker.setIcon(icon);
  }, [marker, icon]);

  useEffect(() => {
    if (!marker || !eventHandlers) return undefined;
    marker.on(eventHandlers);
    return () => {
      marker.off(eventHandlers);
    };
  }, [marker, eventHandlers]);

  return marker ? <MarkerContext.Provider value={marker}>{children}</MarkerContext.Provider> : null;
}

/** Popup bound to the parent <Marker>; children are rendered into the popup element through a portal. */
export function Popup({ children }: { children?: ReactNode }) {
  const marker = useContext(MarkerContext);
  const [el] = useState(() => document.createElement('div'));
  useEffect(() => {
    if (!marker) return undefined;
    marker.bindPopup(el);
    return () => {
      marker.unbindPopup();
    };
  }, [marker, el]);
  return createPortal(children, el);
}

interface PolylineProps {
  positions: Array<[number, number]>;
  pathOptions?: L.PolylineOptions;
}

export function Polyline({ positions, pathOptions }: PolylineProps) {
  const map = useMap();
  const [line, setLine] = useState<L.Polyline | null>(null);
  useEffect(() => {
    const l = L.polyline(positions, pathOptions).addTo(map);
    setLine(l);
    return () => {
      setLine(null);
      l.remove();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [map]);
  useEffect(() => {
    line?.setLatLngs(positions);
  }, [line, positions]);
  useEffect(() => {
    if (line && pathOptions) line.setStyle(pathOptions);
  }, [line, pathOptions]);
  return null;
}

interface GeoJSONProps {
  data: GeoJSON.GeoJsonObject;
  style?: L.PathOptions | L.StyleFunction;
  onEachFeature?: (feature: GeoJSON.Feature, layer: L.Layer) => void;
  pointToLayer?: (feature: GeoJSON.Feature, latlng: L.LatLng) => L.Layer;
}

/** Re-creates the layer whenever `data` changes (callers also pass a `key` for full remounts). */
export function GeoJSON({ data, style, onEachFeature, pointToLayer }: GeoJSONProps) {
  const map = useMap();
  const optsRef = useRef({ style, onEachFeature, pointToLayer });
  optsRef.current = { style, onEachFeature, pointToLayer };
  useEffect(() => {
    const o = optsRef.current;
    const layer = L.geoJSON(data, {
      style: o.style,
      onEachFeature: o.onEachFeature ? (f, l) => o.onEachFeature?.(f as GeoJSON.Feature, l) : undefined,
      pointToLayer: o.pointToLayer ? (f, ll) => (o.pointToLayer as NonNullable<GeoJSONProps['pointToLayer']>)(f as GeoJSON.Feature, ll) : undefined,
    }).addTo(map);
    return () => {
      layer.remove();
    };
  }, [map, data]);
  return null;
}
