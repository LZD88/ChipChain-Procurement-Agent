"""Build explicitly positioned, single-label Yuque boards and layout previews.

No auto-layout DSL round-trip is used for the styled board payloads. Coordinates,
HTML line breaks and edge control points are retained as native board JSON.
"""
from pathlib import Path
import heapq
import html
import json
import math
import re
import xml.etree.ElementTree as ET

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).parent
OUT = ROOT / "styled"
OUT.mkdir(exist_ok=True)
FONT = "/System/Library/Fonts/STHeiti Light.ttc"
FONT18 = ImageFont.truetype(FONT, 18)
FONT15 = ImageFont.truetype(FONT, 15)


def wrap(text, width=246, font=FONT18):
    result = []
    for paragraph in text.replace("<br>", "\n").replace("\\n", "\n").splitlines():
        line = ""
        for char in re.findall(r"[A-Za-z_][A-Za-z0-9_./*-]*|[0-9]+|.", paragraph):
            if line and font.getlength(line + char) > width:
                result.append(line.rstrip())
                line = ""
            if line or not char.isspace():
                line += char
        result.append(line.rstrip())
    return result or [""]


def markup(lines, size=18):
    return (f'<div style="font-size:{size}px;line-height:1.4;text-align:center;'
            'font-weight:400;color:#172B4D;text-shadow:none;">'
            + "<br/>".join(html.escape(s) for s in lines) + "</div>")


def rect(c, margin=0):
    return (c["x"] - margin, c["y"] - margin,
            c["x"] + c["width"] + margin, c["y"] + c["height"] + margin)


def overlap(a, b):
    return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]


def compress(points):
    out = []
    for p in points:
        if out and p == out[-1]:
            continue
        while len(out) >= 2 and ((out[-2][0] == out[-1][0] == p[0])
                                or (out[-2][1] == out[-1][1] == p[1])):
            out.pop()
        out.append(p)
    return out


def port(c, direction):
    x, y, w, h = (c[k] for k in ["x", "y", "width", "height"])
    return {"N": (x + w // 2, y), "S": (x + w // 2, y + h),
            "W": (x, y + h // 2), "E": (x + w, y + h // 2)}[direction]


def route(a, b, nodes, used):
    dx, dy = b["x"] - a["x"], b["y"] - a["y"]
    if abs(dy) < 50:
        source, target = ("E", "W") if dx > 0 else ("W", "E")
    else:
        source, target = ("S", "N") if dy > 0 else ("N", "S")
    vectors = {"N": (0, -1), "S": (0, 1), "W": (-1, 0), "E": (1, 0)}
    p, q = port(a, source), port(b, target)
    v, w = vectors[source], vectors[target]
    start = (p[0] + v[0] * 20, p[1] + v[1] * 20)
    goal = (q[0] + w[0] * 20, q[1] + w[1] * 20)
    bounds = (-60, -60, max(c["x"] + c["width"] for c in nodes) + 100,
              max(c["y"] + c["height"] for c in nodes) + 100)
    obstacles = [rect(c, 8) for c in nodes]
    def blocked(x, y):
        return (x < bounds[0] or x > bounds[2] or y < bounds[1] or y > bounds[3]
                or any(r[0] < x < r[2] and r[1] < y < r[3] for r in obstacles))
    initial = (*start, -1)
    queue, costs, previous = [(0, initial)], {initial: 0}, {}
    found = None
    while queue:
        _, state = heapq.heappop(queue)
        x, y, last = state
        if (x, y) == goal:
            found = state
            break
        for direction, (vx, vy) in enumerate([(1, 0), (-1, 0), (0, 1), (0, -1)]):
            nx, ny = x + vx * 10, y + vy * 10
            if blocked(nx, ny):
                continue
            new = (nx, ny, direction)
            cost = costs[state] + 10 + (14 if last != -1 and last != direction else 0) + used.get((nx, ny), 0) * 18
            if cost < costs.get(new, float("inf")):
                costs[new], previous[new] = cost, state
                heapq.heappush(queue, (cost + abs(nx - goal[0]) + abs(ny - goal[1]), new))
    if found is None:
        raise ValueError(f"Cannot route {a['id']} to {b['id']}")
    path = []
    while found != initial:
        point = found[:2]
        path.append(point)
        used[point] = used.get(point, 0) + 1
        found = previous[found]
    path.append(start)
    path.reverse()
    return source, target, compress([p, *path, q])


def build_flow(path):
    tree = ET.parse(path)
    all_cells = tree.findall(".//mxCell")
    nodes = []
    for c in all_cells:
        if c.get("vertex") != "1":
            continue
        g = c.find("mxGeometry")
        col = min(2, round((float(g.get("x")) - 40) / 300))
        row = round((float(g.get("y")) - 40) / 160)
        lines = wrap(c.get("value"))
        h = max(100, math.ceil((len(lines) * 25 + 26) / 20) * 20)
        style = c.get("style", "")
        shape = "decision" if "rhombus;" in style else "start-end" if "ellipse;" in style else "process"
        if shape == "decision":
            h = max(120, h)
        color = re.search(r"fillColor=([^;]+)", style).group(1)
        nodes.append({"id": c.get("id"), "type": "geometry", "shape": shape,
                      "category": "flowchart", "x": 40 + col * 380, "y": 40 + row * 180,
                      "width": 280, "height": h, "html": markup(lines), "fill": {"color": color},
                      "stroke": {"color": "#AAB8C8", "width": 1},
                      "defaultContentStyle": {"color": "#172B4D", "fontSize": 18}})
    ids = {c["id"]: c for c in nodes}
    for i, a in enumerate(nodes):
        assert not any(overlap(rect(a), rect(b)) for b in nodes[i + 1:]), path.name
    used, edges = {}, []
    routes = {}
    edge_labels = {}
    for c in all_cells:
        if c.get("edge") != "1":
            continue
        a, b = ids[c.get("source")], ids[c.get("target")]
        source, target, points = route(a, b, nodes, used)
        key = c.get("id")
        routes[key] = points
        edge_labels[key] = c.get("value", "")
        edges.append({"id": key, "type": "line", "shape": "straight",
                      "source": {"id": a["id"], "connection": source},
                      "target": {"id": b["id"], "connection": target, "marker": "arrow"},
                      "controlPoints": [list(p) for p in points[1:-1]],
                      "stroke": {"color": "#738399", "width": 1.5}})
    labels = []
    for key, label in edge_labels.items():
        if not label:
            continue
        lines = wrap(label, width=136, font=FONT15)
        width = math.ceil(max(FONT15.getlength(t) for t in lines)) + 8
        height = len(lines) * 21 + 8
        candidates = []
        for a, b in zip(routes[key], routes[key][1:]):
            distance = abs(a[0] - b[0]) + abs(a[1] - b[1])
            if distance < 35:
                continue
            for fraction in [.5, .3, .7]:
                cx, cy = a[0] + (b[0] - a[0]) * fraction, a[1] + (b[1] - a[1]) * fraction
                for ox, oy in [(0, 0), (0, -18), (0, 18), (0, -65), (0, 65),
                               (0, -85), (0, 85), (width/2+6, 0), (-width/2-6, 0)]:
                    candidate = {"x": cx - width/2 + ox, "y": cy - height/2 + oy, "width": width, "height": height}
                    if any(overlap(rect(candidate, 3), rect(c, 3)) for c in nodes + labels):
                        continue
                    crossings = 0
                    for other_key, points in routes.items():
                        if other_key == key:
                            continue
                        for u, v in zip(points, points[1:]):
                            seg = (min(u[0], v[0])-1, min(u[1], v[1])-1,
                                   max(u[0], v[0])+1, max(u[1], v[1])+1)
                            crossings += overlap(rect(candidate), seg)
                    candidates.append((crossings * 1000 + abs(ox) + abs(oy) - distance * .02, candidate))
        if not candidates:
            raise ValueError(f"No clear label location: {path.name} {label}")
        chosen = min(candidates, key=lambda item: item[0])[1]
        labels.append({"id": key + "-label", "type": "text", **chosen,
                       "html": markup(lines, 15), "fill": {"color": "#FFFFFF"},
                       "defaultContentStyle": {"color": "#334155", "fontSize": 15}})
    return nodes, edges, labels, routes


def build_architecture(path):
    root = ET.parse(path)
    cells = root.findall(".//mxCell")
    nodes, labels = [], []
    for c in cells:
        if not c.get("id", "").startswith("layer"):
            continue
        i = int(c.get("id")[5:])
        title = c.get("value")
        y = 30 + i * 200
        nodes.append({"id": c.get("id"), "type": "geometry", "shape": "rounded-rect",
                      "x": 20, "y": y, "width": 940, "height": 182, "round": 8,
                      "fill": {"color": "#F6F8FC"}, "stroke": {"color": "#D8E0EA", "width": 1}})
        labels.append({"id": c.get("id") + "-label", "type": "text", "x": 40, "y": y + 8,
                       "width": 900, "height": 32, "html": markup([title], 19)})
        children = [m for m in cells if m.get("parent") == c.get("id")]
        width = (900 - 20 * (len(children) - 1)) // len(children)
        for j, child in enumerate(children):
            text = child.get("value")
            lines = wrap(text, width=width - 24)
            fill = re.search(r"fillColor=([^;]+)", child.get("style")).group(1)
            nodes.append({"id": child.get("id"), "type": "geometry", "shape": "rounded-rect",
                          "x": 40 + j * (width + 20), "y": y + 50, "width": width, "height": 116,
                          "round": 6, "html": markup(lines), "fill": {"color": fill},
                          "stroke": {"color": "#AAB8C8", "width": 1}})
    return nodes, [], labels, {}


def draw_preview(name, nodes, edges, labels, routes):
    cells = nodes + labels
    maxx = max(c["x"] + c["width"] for c in cells) + 50
    maxy = max(c["y"] + c["height"] for c in cells) + 50
    minx = min([0] + [x for points in routes.values() for x, y in points]) - 20
    miny = min([0] + [y for points in routes.values() for x, y in points]) - 20
    maxx = max([maxx] + [x+25 for points in routes.values() for x,y in points])
    maxy = max([maxy] + [y+25 for points in routes.values() for x,y in points])
    im = Image.new("RGB", (math.ceil(maxx-minx), math.ceil(maxy-miny)), "white")
    draw = ImageDraw.Draw(im)
    for edge in edges:
        points = [(x-minx,y-miny) for x,y in routes[edge['id']]]
        draw.line(points, fill="#738399", width=2)
        if len(points) > 1:
            a,b=points[-2:];angle=math.atan2(b[1]-a[1],b[0]-a[0])
            triangle=[b,(b[0]-10*math.cos(angle-.45),b[1]-10*math.sin(angle-.45)),
                      (b[0]-10*math.cos(angle+.45),b[1]-10*math.sin(angle+.45))]
            draw.polygon(triangle,fill="#738399")
    for c in cells:
        x,y,w,h=c['x']-minx,c['y']-miny,c['width'],c['height']
        fill=c.get('fill',{}).get('color','#FFFFFF')
        if c['type']=='geometry':
            if c['shape']=='decision':
                draw.polygon([(x+w/2,y),(x+w,y+h/2),(x+w/2,y+h),(x,y+h/2)],fill=fill,outline='#AAB8C8')
            else:
                draw.rounded_rectangle((x,y,x+w,y+h),radius=16 if c['shape']=='start-end' else 7,fill=fill,outline='#AAB8C8')
        elif 'fill' in c:
            draw.rectangle((x,y,x+w,y+h),fill=fill)
        if c.get('html'):
            text=html.unescape(re.sub('<[^>]+>','',c['html'].replace('<br/>','\n')))
            font=FONT15 if 'font-size:15px' in c['html'] else FONT18
            bbox=draw.multiline_textbbox((0,0),text,font=font,spacing=6,align='center')
            draw.multiline_text((x+w/2,y+h/2),text,font=font,fill='#172B4D',spacing=6,align='center',anchor='mm')
    im.save(OUT/(name+'.png'))


def main():
    manifest=[]
    for board in json.loads((ROOT/'boards.json').read_text()):
        name=board['name']
        nodes,edges,labels,routes=(build_architecture if board['type']=='architecturediagram' else build_flow)(ROOT/(name+'.drawio'))
        shift_x = min(c['x'] for c in nodes) - 40
        if shift_x:
            for c in nodes + labels:
                c['x'] -= shift_x
            for edge in edges:
                edge['controlPoints'] = [[x-shift_x,y] for x,y in edge.get('controlPoints',[])]
            routes = {key:[(x-shift_x,y) for x,y in points] for key,points in routes.items()}
        ids=[c['id'] for c in nodes+edges+labels]
        assert len(ids)==len(set(ids))
        value={'diagramData':{'head':{'version':'2.0.0','theme':{'name':'default'},'rough':{'name':'default'}},
                              'body':nodes+edges+labels},'viewportOption':'adapt','viewportSetting':None,
               'search':' '.join(html.unescape(re.sub('<[^>]+>',' ',c.get('html',''))) for c in nodes+labels)}
        assert '\\n' not in json.dumps(value,ensure_ascii=False)
        (OUT/(name+'.json')).write_text(json.dumps(value,ensure_ascii=False,indent=2))
        draw_preview(name,nodes,edges,labels,routes)
        manifest.append({'name':name,'section':board['section'],'nodes':len(nodes),'edges':len(edges),'labels':len(labels)})
        print(name,len(nodes),'nodes',len(edges),'edges')
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
