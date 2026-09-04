/** Geo helpers: haversine distance and Gujarat defaults. */

export const GUJARAT_CENTER: [number, number] = [23.2156, 72.6369];
export const GUJARAT_ZOOM = 8;
export const GANDHINAGAR_CENTER: [number, number] = [23.2236, 72.648];

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** Bounds ([south, west], [north, east]) around a set of points with padding. */
export function boundsOf(points: Array<[number, number]>): [[number, number], [number, number]] | null {
  if (!points.length) return null;
  let s = 90;
  let n = -90;
  let w = 180;
  let e = -180;
  for (const [lat, lon] of points) {
    if (lat < s) s = lat;
    if (lat > n) n = lat;
    if (lon < w) w = lon;
    if (lon > e) e = lon;
  }
  const padLat = Math.max(0.005, (n - s) * 0.15);
  const padLon = Math.max(0.005, (e - w) * 0.15);
  return [
    [s - padLat, w - padLon],
    [n + padLat, e + padLon],
  ];
}

export const OSM_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
export const OSM_ATTRIBUTION = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
