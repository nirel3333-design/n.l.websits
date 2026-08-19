/* בדיקות למנוע החישוב של מחשבון הריצוף.
   הרצה:  node ritzuf/test/engine.test.js            */
const fs = require('fs');
const path = require('path');

const html = fs.readFileSync(path.join(__dirname, '..', 'index.html'), 'utf8');
const m = html.match(/<script id="engine">([\s\S]*?)<\/script>/);
if (!m) { console.error('לא נמצא בלוק המנוע'); process.exit(1); }
const sandbox = {};
new Function('window', m[1])(sandbox);
const E = sandbox.TileEngine;

let pass = 0, fail = 0;
function ok(name, cond, extra) {
  if (cond) { pass++; console.log('  ✓ ' + name); }
  else { fail++; console.log('  ✗ ' + name + (extra ? '  → ' + extra : '')); }
}
function near(a, b, e) { return Math.abs(a - b) <= (e === undefined ? 0.01 : e); }
function group(n) { console.log('\n' + n); }

/* ---------- 1. גאומטריה ---------- */
group('גאומטריה');
const sq = [{x:0,y:0},{x:10,y:0},{x:10,y:10},{x:0,y:10}];
ok('שטח ריבוע', near(E.polyArea(sq), 100));
ok('חיתוך למלבן חופף', near(E.polyArea(E.clipToRect(sq, {x:0,y:0,w:5,h:10})), 50));
ok('חיתוך מחוץ לתחום מחזיר ריק', E.clipToRect(sq, {x:20,y:20,w:5,h:5}).length === 0);
const tri = [{x:0,y:0},{x:10,y:0},{x:0,y:10}];
ok('שטח משולש', near(E.polyArea(tri), 50));

/* ---------- 2. פירוק רצפה עם ניכויים ---------- */
group('ניכויים');
let fr = E.floorRegions(400, 300, [{x:100,y:100,w:50,h:50}]);
let area = fr.reduce((s,r)=>s+r.w*r.h,0);
ok('שטח נטו = ברוטו פחות ניכוי', near(area, 400*300 - 2500, 0.1), area);
fr = E.floorRegions(400, 300, []);
ok('בלי ניכויים = מלבן אחד', fr.length === 1 && near(fr[0].w,400) && near(fr[0].h,300));
fr = E.floorRegions(400, 300, [{x:-50,y:-50,w:100,h:100}]);
area = fr.reduce((s,r)=>s+r.w*r.h,0);
ok('ניכוי בפינה נחתך לגבולות החדר', near(area, 120000 - 2500, 0.1), area);

/* ---------- 3. תכנון ציר ---------- */
group('תכנון ציר (planAxis)');
// 400 ס"מ, אריח 60, פוגה 0 -> 6 שלמים (360) ושארית 40 => 20 לכל צד
let p = E.planAxis(400, 60, 0, 'center', 0);
ok('מרכז: היסט = c - tile', near(p.off, 20 - 60), p.off);
ok('מרכז: חיתוך מאוזן 20 ס״מ', near(p.balancedCut, 20), p.balancedCut);
// 370 עם אריח 60 => 6 שלמים 360, שארית 10 -> 5 לכל צד, קטן מ-20 => מוריד אריח
p = E.planAxis(370, 60, 0, 'center', 0);
ok('מרכז: הפעלת כלל ⅓ אריח', p.adjusted === true);
ok('מרכז: חיתוך אחרי תיקון ≥ ⅓', p.balancedCut >= 20 - 1e-9, p.balancedCut);
ok('מרכז: החיתוך המתוקן 35 ס״מ', near(p.balancedCut, 35), p.balancedCut);
// פינה
p = E.planAxis(400, 60, 0, 'corner', 0);
ok('פינה: היסט 0', near(p.off, 0));
// נרמול היסט לטווח (-pitch, 0]
p = E.planAxis(400, 60, 0.3, 'corner', 145);
ok('היסט מנורמל לטווח', p.off <= 1e-9 && p.off > -p.pitch, p.off);
// התאמה מדויקת ללא חיתוכים: 4 אריחים 60 + 3 פוגות 0.3 = 240.9
p = E.planAxis(240.9, 60, 0.3, 'center', 0);
ok('התאמה מדויקת = ללא חיתוך', near(p.balancedCut, 0, 0.005) && near(p.off, 0), p.balancedCut);
// minShift
ok('minShift בטווח חצי־צעד', Math.abs(E.minShift(-55, -5, 60)) <= 30 + 1e-9);

/* ---------- 4. פריסה ישרה ---------- */
group('פריסה ישרה');
let L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:0,
  pattern:'grid', start:'corner', shiftX:0, shiftY:0, deductions:[]});
ok('אין שגיאה', !L.error, L.error);
let cover = L.tiles.reduce((s,t)=>s+t.area,0);
ok('כיסוי מלא של הרצפה', near(cover, 400*300, 1), cover);
// 400/60 = 6 שלמים + 40 חתוך ; 300/60 = 5 שלמים בדיוק
ok('שלמים 6×5=30', L.fullCount === 30, L.fullCount);
ok('חתוכים 5 (טור אחד)', L.cutCount === 5, L.cutCount);

let A = E.analyze(L, {wastePct:10, perPack:4});
ok('נדרש = 35', A.need === 35, A.need);
ok('כולל 10% פחת = 39', A.withWaste === 39, A.withWaste);
ok('חבילות = 10', A.packs === 10, A.packs);
ok('שטח 12 מ״ר', near(A.areaM2, 12));
ok('חיתוך יחיד בקיר ימין 40 ס״מ',
   A.cuts.length === 1 && near(A.cuts[0].w, 40) && A.cuts[0].n === 5 && A.cuts[0].place === 'קיר ימין',
   JSON.stringify(A.cuts));
ok('אזהרה: חיתוך צר? (40 > 20 — אין)', A.warnings.filter(w=>w.type==='narrow').length === 0);

/* ---------- 5. סימטריה במרכז ---------- */
group('פריסה ממרכז החדר');
L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:3,
  pattern:'grid', start:'center', shiftX:0, shiftY:0, deductions:[]});
A = E.analyze(L, {wastePct:10, perPack:4});
ok('חיתוכי שמאל/ימין שווים', near(A.walls.left.min, A.walls.right.min, 0.05),
   A.walls.left.min + ' vs ' + A.walls.right.min);
ok('חיתוכי עליון/תחתון שווים', near(A.walls.top.min, A.walls.bottom.min, 0.05),
   A.walls.top.min + ' vs ' + A.walls.bottom.min);
ok('אין אזהרת חיתוך צר', A.warnings.filter(w=>w.type==='narrow').length === 0,
   JSON.stringify(A.warnings));
ok('כל חיתוכי הקצה ≥ ⅓ אריח', A.walls.left.min >= 20 - 0.05, A.walls.left.min);
cover = L.tiles.reduce((s,t)=>s+t.area,0);
ok('כיסוי כמעט מלא (היתרה = פוגות)', cover > 400*300*0.96 && cover <= 400*300 + 1, cover);

/* ---------- 6. כלל ⅓ אריח בפריסה מפינה ---------- */
group('כלל ⅓ אריח');
// 370×300 מפינה: החיתוך בקיר ימין = 10 ס"מ < 20 => אזהרה + המלצת הזזה
L = E.buildLayout({roomW:370, roomH:300, tileW:60, tileH:60, groutMm:0,
  pattern:'grid', start:'corner', shiftX:0, shiftY:0, deductions:[]});
A = E.analyze(L, {wastePct:10, perPack:4});
let nw = A.warnings.filter(w=>w.type==='narrow');
ok('זוהה חיתוך צר', nw.length === 1, JSON.stringify(A.warnings));
ok('ההמלצה כוללת הזזה בס״מ', nw.length && nw[0].shift > 0, nw.length && nw[0].shift);
// החלת ההמלצה מבטלת את האזהרה
if (nw.length) {
  let L2 = E.buildLayout({roomW:370, roomH:300, tileW:60, tileH:60, groutMm:0,
    pattern:'grid', start:'corner', shiftX:nw[0].dir, shiftY:0, deductions:[]});
  let A2 = E.analyze(L2, {wastePct:10, perPack:4});
  ok('אחרי ההזזה — אין חיתוך צר', A2.warnings.filter(w=>w.type==='narrow').length === 0,
     JSON.stringify(A2.warnings));
  ok('אחרי ההזזה — צדדים שווים', near(A2.walls.left.min, A2.walls.right.min, 0.05),
     A2.walls.left.min + ' vs ' + A2.walls.right.min);
  ok('אחרי ההזזה — החיתוך 35 ס״מ', near(A2.walls.left.min, 35, 0.05), A2.walls.left.min);
}

/* ---------- 7. פוגה לא נספרת בקיר ---------- */
group('פוגות');
// 4 אריחים 60 + 3 פוגות 5 מ"מ = 241.5 -> בלי חיתוכים בכלל
L = E.buildLayout({roomW:241.5, roomH:241.5, tileW:60, tileH:60, groutMm:5,
  pattern:'grid', start:'center', shiftX:0, shiftY:0, deductions:[]});
ok('התאמה מושלמת: 16 שלמים, 0 חתוכים', L.fullCount === 16 && L.cutCount === 0,
   L.fullCount + '/' + L.cutCount);

/* ---------- 8. בריק ושליש ---------- */
group('דגמים');
L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:3,
  pattern:'brick', start:'center', shiftX:0, shiftY:0, deductions:[]});
cover = L.tiles.reduce((s,t)=>s+t.area,0);
ok('בריק: כיסוי הרצפה מלא (פחות פוגות)', cover > 400*300*0.97 && cover <= 400*300 + 1, cover);
ok('בריק: יש חיתוכים בשורות המוזזות', L.cutCount > 0, L.cutCount);
L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:3,
  pattern:'third', start:'center', shiftX:0, shiftY:0, deductions:[]});
ok('שליש: נבנה בלי שגיאה', !L.error && L.tiles.length > 0);

/* ---------- 9. אלכסון ---------- */
group('אלכסון 45°');
L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:3,
  pattern:'diag', start:'center', shiftX:0, shiftY:0, deductions:[]});
ok('אלכסון: נבנה', !L.error && L.tiles.length > 0, L.error);
cover = L.tiles.reduce((s,t)=>s+t.area,0);
ok('אלכסון: כיסוי סביר', cover > 400*300*0.95 && cover <= 400*300 + 1, cover);
ok('אלכסון: יש חיתוכים משולשים', L.tiles.some(t=>!t.full && t.shape === 'משולש'));
A = E.analyze(L, {wastePct:10, perPack:4});
ok('אלכסון: סה״כ = שלמים + חתוכים', A.need === L.fullCount + L.cutCount);

/* ---------- 10. ניכויים בפריסה ---------- */
group('פריסה עם ניכוי');
L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:0,
  pattern:'grid', start:'corner', shiftX:0, shiftY:0,
  deductions:[{name:'עמוד', x:120, y:120, w:60, h:60}]});
ok('אריח שנופל בדיוק על העמוד לא נספר', L.fullCount === 29, L.fullCount);
cover = L.tiles.reduce((s,t)=>s+t.area,0);
ok('שטח מכוסה = חדר פחות עמוד', near(cover, 400*300 - 3600, 1), cover);
A = E.analyze(L, {wastePct:10, perPack:4});
L = E.buildLayout({roomW:400, roomH:300, tileW:60, tileH:60, groutMm:0,
  pattern:'grid', start:'corner', shiftX:0, shiftY:0,
  deductions:[{name:'ארון', x:100, y:100, w:50, h:50}]});
A = E.analyze(L, {wastePct:10, perPack:4});
ok('חיתוכים סביב הניכוי מסומנים בשמו', A.cuts.some(c=>c.place === 'סביב ארון'),
   JSON.stringify(A.cuts.map(c=>c.place)));

/* ---------- 11. חדר קטן מאריח ---------- */
group('מקרי קצה');
L = E.buildLayout({roomW:40, roomH:40, tileW:60, tileH:60, groutMm:3,
  pattern:'grid', start:'center', shiftX:0, shiftY:0, deductions:[]});
ok('חדר קטן מאריח: יש חתיכות בלבד', !L.error && L.fullCount === 0 && L.cutCount > 0,
   L.fullCount + '/' + L.cutCount);
L = E.buildLayout({roomW:0, roomH:300, tileW:60, tileH:60, groutMm:3,
  pattern:'grid', start:'center', shiftX:0, shiftY:0, deductions:[]});
ok('מידה 0 מחזירה שגיאה', !!L.error);


/* ---------- 12. איזון חכם בבריק (שתי משפחות שורות) ---------- */
group('איזון בריק');
{
  const cfg = {roomW:520, roomH:430, tileW:120, tileH:60, groutMm:3,
    pattern:'brick', start:'center', shiftX:0, shiftY:0, deductions:[]};
  let Lb = E.buildLayout(cfg);
  let Ab = E.analyze(Lb, {wastePct:10, perPack:2});
  // מפעילים את ההמלצות עד שאין אזהרה (כמו האיזון האוטומטי בממשק)
  let guard = 0;
  while (guard++ < 4) {
    const nb = Ab.warnings.filter(w=>w.type==='narrow' && Math.abs(w.dir)>0.05);
    if (!nb.length) break;
    nb.forEach(w=>{ if (w.axis==='X') cfg.shiftX += w.dir; else cfg.shiftY += w.dir; });
    Lb = E.buildLayout(cfg); Ab = E.analyze(Lb, {wastePct:10, perPack:2});
  }
  const narrow = Ab.warnings.filter(w=>w.type==='narrow');
  ok('בריק: האיזון האוטומטי מסלק את החיתוך הצר', narrow.length === 0,
     JSON.stringify(narrow.map(w=>w.msg)));
  ok('בריק: כל חיתוכי הקצה ≥ ⅓ אריח',
     ['left','right'].every(k=>!Ab.walls[k].has || Ab.walls[k].min >= 40 - 0.05) &&
     ['top','bottom'].every(k=>!Ab.walls[k].has || Ab.walls[k].min >= 20 - 0.05),
     JSON.stringify(Object.keys(Ab.walls).map(k=>k+':'+Ab.walls[k].min)));
}

/* ---------- 12ב. כיבוי כלל ה־⅓ ---------- */
group('כיבוי הכלל (avoidNarrow=false)');
{
  const on  = E.planAxis(370, 60, 0, 'center', 0, true);
  const off = E.planAxis(370, 60, 0, 'center', 0, false);
  ok('כשהכלל פעיל — החיתוך 35', near(on.balancedCut, 35), on.balancedCut);
  ok('כשהכלל כבוי — החיתוך 5 (פריסה גיאומטרית טהורה)', near(off.balancedCut, 5), off.balancedCut);
  const Lo = E.buildLayout({roomW:370, roomH:300, tileW:60, tileH:60, groutMm:0,
    pattern:'grid', start:'center', shiftX:0, shiftY:0, deductions:[], avoidNarrow:false});
  const Ao = E.analyze(Lo, {wastePct:10, perPack:4});
  const nn = Ao.warnings.filter(w=>w.type==='narrow');
  ok('כשהכלל כבוי — עדיין מוצגת אזהרה', nn.length === 1, JSON.stringify(Ao.warnings));
  ok('והאזהרה כוללת המלצת הזזה מעשית', nn.length && nn[0].shift > 0, nn.length && nn[0].shift);
}

/* ---------- 13. edgeCuts / bestOffset ---------- */
group('חיתוכי קצה');
let ec = E.edgeCuts(400, 60, 0, 0);
ok('מפינה: שמאל שלם, ימין 40', ec.l === 0 && near(ec.r, 40), JSON.stringify(ec));
ec = E.edgeCuts(400, 60, 0, -40);
ok('אחרי הזזה: 20 בשני הצדדים', near(ec.l, 20) && near(ec.r, 20), JSON.stringify(ec));
ec = E.edgeCuts(240.9, 60, 0.3, 0);
ok('התאמה מדויקת: אין חיתוכים', ec.l === 0 && ec.r === 0, JSON.stringify(ec));
let bo = E.bestOffset(370, 60, 0, [0], 0);
ok('bestOffset מגיע ל־⅓ אריח לפחות', bo.min >= 20 - 0.05, bo.min);
bo = E.bestOffset(520, 120, 0.3, [0, (120.3)/2], 0);
ok('bestOffset מכסה גם את שורות הבריק', bo.min >= 40 - 0.05, bo.min);

console.log('\n────────────');
console.log((fail ? '✗ ' : '✓ ') + pass + ' עברו, ' + fail + ' נכשלו');
process.exit(fail ? 1 : 0);
