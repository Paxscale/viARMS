"""Generate the self-contained landing page product tour using headless Chrome."""

from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "static" / "media" / "viarms-product-tour.webm"
POSTER = ROOT / "static" / "media" / "viarms-product-tour-poster.png"
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")

PAGE = r'''<!doctype html>
<html><head><meta charset="utf-8"><style>
*{box-sizing:border-box}html,body{margin:0;background:#070b14;color:#fff;font:16px Arial,sans-serif}
canvas{display:block;width:1280px;height:720px}
</style></head><body><canvas id="c" width="1280" height="720"></canvas><script>
const c=document.querySelector('#c'),x=c.getContext('2d'),W=1280,H=720;
const scenes=[
 {tag:'ONE SIMPLE WORKSPACE',title:'Your shop, at a glance',sub:'A clear view of today’s business, all in one place.',kind:'dashboard'},
 {tag:'POINT OF SALE',title:'Serve customers with confidence',sub:'Find products, build the sale, and check out smoothly.',kind:'pos'},
 {tag:'SMARTER INVENTORY',title:'Know what is in stock',sub:'Track quantities, batches, and products that need attention.',kind:'inventory'},
 {tag:'SALES & REPORTS',title:'Make decisions with clarity',sub:'See sales activity and understand how your business is doing.',kind:'reports'},
 {tag:'GROW WITH YOUR TEAM',title:'Every branch, in sync',sub:'Manage your locations and give your staff the tools to help.',kind:'branches'}
];
const total=25000, fps=30, duration=total/1000;
function rr(left,top,w,h,r,fill,stroke){x.beginPath();x.roundRect(left,top,w,h,r);if(fill){x.fillStyle=fill;x.fill()}if(stroke){x.strokeStyle=stroke;x.lineWidth=1;x.stroke()}}
function txt(s,xp,yp,size=15,color='#eef2ff',weight=400){x.font=`${weight} ${size}px Arial`;x.fillStyle=color;x.fillText(s,xp,yp)}
function line(x1,y1,x2,y2,color='#243149',width=1){x.beginPath();x.moveTo(x1,y1);x.lineTo(x2,y2);x.strokeStyle=color;x.lineWidth=width;x.stroke()}
function windowFrame(kind,progress){
 x.save();x.translate(0,132);x.scale(1,.8);
 const bx=190,by=120,bw=900,bh=495;
 rr(bx,by,bw,bh,18,'#111827','#2b374c');
 x.fillStyle='#0b1220';x.fillRect(bx,by,bw,54);
 rr(bx+20,by+17,20,20,6,'#6366f1');txt('v',bx+26,by+32,14,'#fff',700);txt('viARMS',bx+49,by+33,15,'#f8fafc',700);
 rr(bx+bw-195,by+13,178,28,8,'#182235');txt('Main branch',bx+bw-177,by+32,12,'#cbd5e1');
 x.fillStyle='#0d1524';x.fillRect(bx,by+54,178,bh-54);
 const nav=[['▦','Overview'],['▤','Point of sale'],['▧','Products'],['▥','Sales'],['⚙','Settings']];
 nav.forEach((a,i)=>{let yy=by+88+i*48;if((kind==='pos'&&i===1)||(kind==='inventory'&&i===2)||(kind==='reports'&&i===3)||(kind==='dashboard'&&i===0)||(kind==='branches'&&i===0)){rr(bx+12,yy-23,152,36,8,'#242650');}txt(a[0],bx+25,yy,15,'#9ba8ff',600);txt(a[1],bx+52,yy,13,'#bec9da')});
 let px=bx+205, py=by+82;
 if(kind==='dashboard'){
   txt('Good morning, Amara',px,py,21,'#f7f8ff',700);txt('Here is what is happening in your store today.',px,py+24,12,'#8fa0b8');
   const cards=[['TODAY’S SALES','₦284,500','#34d399'],['ORDERS','48','#818cf8'],['LOW STOCK','6 items','#fbbf24']];
   cards.forEach((a,i)=>{let xx=px+i*214;rr(xx,py+49,198,91,12,'#172235','#26344a');txt(a[0],xx+15,py+75,10,'#93a2b8',700);txt(a[1],xx+15,py+112,23,a[2],700)});
   rr(px,py+158,630,194,12,'#172235','#26344a');txt('Sales overview',px+17,py+185,14,'#e8edf8',600);txt('This week',px+540,py+184,10,'#9aa9c0');
   for(let i=0;i<5;i++){let yy=py+215+i*25;line(px+18,yy,px+604,yy,'#28364a');}
   let pts=[0.62,0.49,0.68,0.43,0.56,0.28,0.39,0.21];x.beginPath();pts.forEach((v,i)=>{let xx=px+30+i*80,yy=py+210+v*125;i?x.lineTo(xx,yy):x.moveTo(xx,yy)});x.strokeStyle='#818cf8';x.lineWidth=4;x.stroke();
 }else if(kind==='pos'){
   txt('Point of sale',px,py,21,'#f7f8ff',700);rr(px,py+24,405,37,9,'#172235','#29374c');txt('⌕  Search products by name or SKU',px+14,py+48,11,'#8fa0b8');
   const products=[['Rice 5kg','₦8,500'],['Cooking oil 2L','₦6,200'],['Tomato paste','₦1,250'],['Milk powder','₦4,800']];
   products.forEach((p,i)=>{let xx=px+(i%2)*205,yy=py+77+Math.floor(i/2)*108;rr(xx,yy,190,94,11,'#172235','#29374c');rr(xx+12,yy+13,42,42,9,['#a78bfa','#fbbf24','#ef7185','#34d399'][i]+'33');txt('▦',xx+25,yy+40,18,['#c4b5fd','#fcd34d','#fda4af','#6ee7b7'][i]);txt(p[0],xx+63,yy+29,12,'#e4eaf5',600);txt(p[1],xx+63,yy+53,12,'#a5b4fc',700);txt('+ Add',xx+139,yy+77,10,'#93a2b8')});
   rr(px+430,py+24,235,370,13,'#172235','#29374c');txt('Current sale',px+450,py+53,15,'#f2f5fb',700);txt('Rice 5kg',px+450,py+100,12,'#dce4f1');txt('₦8,500',px+605,py+100,12,'#dce4f1');txt('Cooking oil 2L',px+450,py+140,12,'#dce4f1');txt('₦6,200',px+605,py+140,12,'#dce4f1');line(px+450,py+164,px+645,py+164);txt('Total',px+450,py+205,13,'#aab8cc');txt('₦14,700',px+565,py+205,18,'#fff',700);rr(px+450,py+235,195,42,9,'#6366f1');txt('Complete sale',px+500,py+261,13,'#fff',700);
 }else if(kind==='inventory'){
   txt('Products',px,py,21,'#f7f8ff',700);txt('Keep your catalog and stock up to date.',px,py+24,12,'#8fa0b8');rr(px+490,py-5,160,34,8,'#6366f1');txt('+  Add product',px+516,py+17,12,'#fff',700);
   rr(px,py+50,665,343,12,'#172235','#29374c');x.fillStyle='#202c40';x.fillRect(px+1,py+51,663,40);['PRODUCT','SKU','STOCK','STATUS'].forEach((v,i)=>txt(v,px+[18,275,410,540][i],py+76,10,'#9caac0',700));
   const rows=[['Rice 5kg','FOOD-001','24','In stock'],['Cooking oil 2L','FOOD-018','8','In stock'],['Tomato paste','FOOD-027','3','Low stock'],['Milk powder','FOOD-043','0','Out of stock'],['Bath soap','CARE-004','31','In stock'],['Bottled water','DRINK-011','6','Low stock']];
   rows.forEach((r,i)=>{let yy=py+123+i*43;line(px+15,yy+12,px+650,yy+12,'#27354a');txt(r[0],px+18,yy,12,'#e1e8f3');txt(r[1],px+275,yy,11,'#9aa9be');txt(r[2],px+410,yy,12,'#e1e8f3');txt(r[3],px+540,yy,10,r[3]==='In stock'?'#6ee7b7':'#fbbf24',600)});
 }else if(kind==='reports'){
   txt('Sales history',px,py,21,'#f7f8ff',700);txt('Review transactions and track performance.',px,py+24,12,'#8fa0b8');
   const cards=[['GROSS SALES','₦1,842,600'],['TRANSACTIONS','326'],['AVG. SALE','₦5,652']];cards.forEach((a,i)=>{let xx=px+i*220;rr(xx,py+49,202,78,11,'#172235','#29374c');txt(a[0],xx+15,py+75,10,'#92a2b8',700);txt(a[1],xx+15,py+108,18,'#f1f5fb',700)});
   rr(px,py+146,650,239,12,'#172235','#29374c');txt('Recent transactions',px+17,py+173,14,'#edf1f8',600);const rows=[['Today, 10:42 AM','Receipt #1048','₦18,400'],['Today, 10:26 AM','Receipt #1047','₦6,250'],['Today, 9:58 AM','Receipt #1046','₦32,000'],['Today, 9:41 AM','Receipt #1045','₦4,800']];rows.forEach((r,i)=>{let yy=py+211+i*40;line(px+15,yy+11,px+630,yy+11,'#27354a');txt(r[0],px+18,yy,11,'#b4c0d1');txt(r[1],px+230,yy,11,'#b4c0d1');txt(r[2],px+530,yy,11,'#e7edf6',600)});
 }else{
   txt('Your shops',px,py,21,'#f7f8ff',700);txt('See your branches and teams in one place.',px,py+24,12,'#8fa0b8');
   const shops=[['Central Market','Lagos','12 team members','#818cf8'],['Ikeja Branch','Lagos','8 team members','#34d399'],['Ring Road','Ibadan','5 team members','#fbbf24']];
   shops.forEach((a,i)=>{let yy=py+54+i*105;rr(px,yy,650,88,12,'#172235','#29374c');rr(px+16,yy+18,48,48,12,a[3]+'33');txt('⌂',px+31,yy+49,20,a[3],700);txt(a[0],px+80,yy+34,14,'#eff3fa',700);txt(a[1]+'  ·  '+a[2],px+80,yy+59,11,'#9aa9be');rr(px+520,yy+26,106,32,8,'#1c293d');txt('View branch',px+539,yy+47,10,'#bdc8d8')});
 }
 x.restore();
}
function draw(t){
 const idx=Math.min(scenes.length-1,Math.floor(t/5));const local=(t%5)/5,s=scenes[idx];
 const grad=x.createLinearGradient(0,0,W,H);grad.addColorStop(0,'#080d19');grad.addColorStop(.55,'#0d1424');grad.addColorStop(1,'#11112a');x.fillStyle=grad;x.fillRect(0,0,W,H);
 x.fillStyle='rgba(99,102,241,.08)';x.beginPath();x.arc(1080,30,370,0,Math.PI*2);x.fill();
 rr(70,46,30,30,9,'#6366f1');txt('v',79,68,20,'#fff',700);txt('viARMS',111,67,18,'#f8fafc',700);txt('RETAIL MANAGEMENT, SIMPLIFIED',930,66,11,'#99a8c0',600);
 const fade=Math.min(1,local*5,(1-local)*5);x.globalAlpha=Math.max(.15,fade);
 txt(s.tag,78,148,12,'#a5b4fc',700);txt(s.title,78,195,31,'#fff',700);txt(s.sub,78,222,15,'#b6c2d5');
 windowFrame(s.kind,local);x.globalAlpha=1;
 rr(78,654,1124,2,1,'#28334a');rr(78,654,1124*((idx+local)/scenes.length),2,1,'#818cf8');
 txt('SELL  ·  MANAGE  ·  GROW',78,684,10,'#7888a2',700);txt(String(idx+1).padStart(2,'0')+' / 05',1145,684,11,'#aebbd0',600);
}
draw(1);c.toBlob(blob=>fetch('/poster',{method:'POST',headers:{'Content-Type':'image/png'},body:blob}),'image/png');
const stream=c.captureStream(fps);const rec=new MediaRecorder(stream,{mimeType:'video/webm;codecs=vp9',videoBitsPerSecond:2200000});let start;
rec.ondataavailable=async e=>{if(e.data.size){try{await fetch('/save',{method:'POST',headers:{'Content-Type':'video/webm'},body:e.data})}catch(_){}}};
rec.onstop=()=>document.title='READY';
function frame(now){if(!start)start=now;let elapsed=now-start;draw(elapsed/1000);if(elapsed<total){requestAnimationFrame(frame)}else{rec.stop();stream.getTracks().forEach(t=>t.stop())}}
rec.start();requestAnimationFrame(frame);
</script></body></html>'''


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(PAGE.encode("utf-8"))

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        target = POSTER if self.path == "/poster" else OUTPUT
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.rfile.read(length))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"saved")

    def log_message(self, *_args):
        pass


def main():
    if not CHROME.exists():
        raise SystemExit(f"Chrome not found: {CHROME}")
    OUTPUT.unlink(missing_ok=True)
    POSTER.unlink(missing_ok=True)
    server = HTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_port
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    with tempfile.TemporaryDirectory(prefix="viarms-tour-") as profile:
        chrome = subprocess.Popen([
            str(CHROME), "--headless=new", "--disable-gpu", "--no-sandbox",
            "--autoplay-policy=no-user-gesture-required", f"--user-data-dir={profile}",
            f"http://127.0.0.1:{port}/",
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        deadline = time.time() + 90
        while time.time() < deadline and not OUTPUT.exists():
            time.sleep(1)
        chrome.terminate()
        try:
            chrome.wait(timeout=5)
        except subprocess.TimeoutExpired:
            chrome.kill()
        server.shutdown()
    if not OUTPUT.exists() or OUTPUT.stat().st_size < 100_000:
        raise SystemExit("Chrome did not produce the walkthrough video")
    print(f"Created {OUTPUT} ({OUTPUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
