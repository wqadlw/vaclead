// bookmarklet under test
(function(){
var LAB={法定代表人:"法人",法人:"法人",负责人:"法人","统一社会信用代码":"信用代码",信用代码:"信用代码",纳税人识别号:"信用代码",注册资本:"注册资本",成立日期:"成立日期",注册日期:"成立日期",企业电话:"电话",联系电话:"电话",电话:"电话",企业地址:"地址",注册地址:"地址",地址:"地址",经营范围:"经营范围"};
var out=[],cur=null,ph="";
var lines=(document.body.innerText||"").split(/\\n+/);
for(var i=0;i<lines.length;i++){
var ln=lines[i].trim();
if(!ln)continue;
if(cur){out.push(cur+" "+ln.slice(0,300));cur=null;continue}
var m=ln.match(/^([一-龥A-Za-z（）()]{2,12})[：\\s]\s*(.+)$/);
if(m&&LAB[m[1]]){out.push(LAB[m[1]]+" "+m[2].trim().slice(0,300));continue}
var m2=ln.match(/^([一-龥]{2,12})$/);
if(m2&&LAB[m2[1]]){cur=LAB[m2[1]];continue}
if(!ph){var pm=ln.match(/1[3-9]\\d{9}|0\\d{2,3}[-—]?\\d{7,8}(?:[-—]\\d{3,4})?/);if(pm)ph=pm[0]}
}
if(ph&&out.every(function(s){return s.indexOf("电话")!==0}))out.push("电话 "+ph);
var q=encodeURIComponent(out.join("\n").slice(0,1800));
var eid=(location.search.match(/eid=(\\d+)/)||[])[1];
if(!eid){alert("请从平台点「一键打开风鸟」进来（链接需带 eid）");return}
window.open("http://127.0.0.1:8000/enterprises/view?id="+eid+"&gongshang="+q);
})()