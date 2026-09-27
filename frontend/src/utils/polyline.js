/** Decode a Google encoded polyline (Strava's route format) into [lat, lng] pairs. */
export function decodePolyline(encoded) {
  const points = [];
  let index = 0;
  let lat = 0;
  let lng = 0;
  while (index < encoded.length) {
    for (const axis of [0, 1]) {
      let result = 0;
      let shift = 0;
      let byte;
      do {
        byte = encoded.charCodeAt(index++) - 63;
        result |= (byte & 0x1f) << shift;
        shift += 5;
      } while (byte >= 0x20);
      const delta = result & 1 ? ~(result >> 1) : result >> 1;
      if (axis === 0) lat += delta;
      else lng += delta;
    }
    points.push([lat / 1e5, lng / 1e5]);
  }
  return points;
}

/** An SVG path for the route, scaled to fit a width x height box (north up, aspect kept). */
export function routePath(points, width, height, pad = 6) {
  if (points.length < 2) return "";
  const midLat = (points.reduce((t, p) => t + p[0], 0) / points.length) * (Math.PI / 180);
  // Longitude degrees shrink towards the poles; scale x by cos(latitude) so shapes aren't stretched.
  const xy = points.map(([la, ln]) => [ln * Math.cos(midLat), -la]);
  const xs = xy.map(p => p[0]);
  const ys = xy.map(p => p[1]);
  const [minX, maxX, minY, maxY] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const scale = Math.min((width - 2 * pad) / (maxX - minX || 1), (height - 2 * pad) / (maxY - minY || 1));
  const offX = (width - (maxX - minX) * scale) / 2;
  const offY = (height - (maxY - minY) * scale) / 2;
  return xy
    .map(([x, y], i) => `${i ? "L" : "M"}${(offX + (x - minX) * scale).toFixed(1)},${(offY + (y - minY) * scale).toFixed(1)}`)
    .join("");
}
