'use strict';
const $ = (s, root=document) => root.querySelector(s);
const esc = value => String(value ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
const svg = (paths) => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths}</svg>`;
const icons = {
  overview: svg('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
  exam: svg('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>'),
  events: svg('<path d="M3 12h4l3-7 4 14 3-7h4"/>'),
  reports: svg('<path d="M14 3H6a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V9z"/><path d="M14 3v6h6M8 13h8M8 17h5"/>'),
  settings: svg('<path d="M4 6h16M4 12h16M4 18h16"/><circle cx="9" cy="6" r="2" fill="currentColor"/><circle cx="16" cy="12" r="2" fill="currentColor"/><circle cx="8" cy="18" r="2" fill="currentColor"/>'),
  help: svg('<circle cx="12" cy="12" r="9"/><path d="M9.3 9a2.8 2.8 0 0 1 5.4 1c0 2-2.7 2-2.7 4m0 3h.01"/>'),
  camera: svg('<rect x="3" y="5" width="14" height="14" rx="3"/><path d="m17 10 4-3v10l-4-3"/>'),
  shield: svg('<path d="m12 3 8 3v6c0 5-8 9-8 9s-8-4-8-9V6z"/><path d="m8 12 3 3 5-6"/>'),
  eye: svg('<path d="M2 12s4-7 10-7 10 7 10 7-4 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/>'),
  clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  phone: svg('<rect x="7" y="2" width="10" height="20" rx="2"/><path d="M10 5h4m-3 14h2"/>'),
  users: svg('<circle cx="9" cy="8" r="3"/><path d="M3 20v-3a6 6 0 0 1 12 0v3M16 5a3 3 0 0 1 0 6m3 9v-3a6 6 0 0 0-3-5"/>'),
  download: svg('<path d="M12 3v12m-4-4 4 4 4-4M4 15v5h16v-5"/>'),
  play: svg('<path d="m8 5 11 7-11 7z"/>'),
  check: svg('<path d="m5 12 4 4L19 6"/>'),
};
const preview = location.protocol === 'file:' || window.QORGAU_PREVIEW === true;
const examSurface = !preview && new URLSearchParams(location.search).get('exam') === '1';
const csrf = $('meta[name=qorgau-token]').content;
let state = null, view = 'choose', role = 'choose', currentQuestion = 0, eventFilter = 'all';
let roleBusy = false, authGeneration = 0;
const studentNav = [['overview','Подготовка'],['exam','Мой тест']];
const teacherNav = [['overview','Мой кабинет'],['settings','Редактор теста'],['events','Журнал событий'],['reports','Результаты']];
let teacherToken = '', sessions = [], selectedReport = null, reportsLoading = false;
let demoCamera = false, demoCalibrated = false, lastStatus = '', connected = true, eventCount = 0;
let toastTimer, polling = false, evidenceURL;
let calibrationWizard = null, calibrationSerial = 0, nativeCalibrationQueue = Promise.resolve(), lastCalibrationDiagnostic = null, calibrationFinishing = null;
let examDraft=null, examLoading=false, examError='', examDirty=false, editorSaving=false, renderedView='', loginNext=null;
const cloneData = value => JSON.parse(JSON.stringify(value));
const mock = {sessions:[], events:[], active:null, camera:false, calibrated:false, next:1, visible:false, participant:null};
const demoQuestions = [
 {id:'q1',text:'Какое свойство транзакции означает «всё или ничего»?',options:['Атомарность','Изолированность','Долговечность','Доступность'],correct:0},
 {id:'q2',text:'Какой SQL-оператор выбирает строки из таблицы?',options:['UPDATE','SELECT','INSERT','CREATE'],correct:1},
 {id:'q3',text:'Что делает первичный ключ в реляционной базе данных?',options:['Шифрует таблицу','Сортирует столбцы','Однозначно определяет запись','Создаёт резервную копию'],correct:2},
 {id:'q4',text:'Какой протокол обеспечивает защищённое соединение с веб-сайтом?',options:['FTP','HTTP','UDP','HTTPS'],correct:3},
 {id:'q5',text:'Как называется обучение на размеченных примерах?',options:['С учителем','Кластеризация','Без учителя','Случайный поиск'],correct:0},
 {id:'q6',text:'Для чего обычно используют индекс базы данных?',options:['Для удаления дубликатов','Для ускорения поиска','Для шифрования паролей','Для замены резервных копий'],correct:1},
 {id:'q7',text:'Что является системой контроля версий?',options:['Docker','PostgreSQL','Git','Nginx'],correct:2},
 {id:'q8',text:'Как называется выдача только необходимых пользователю прав?',options:['Открытый доступ','Общая учётная запись','Полный контроль','Минимальные привилегии'],correct:3},
];
const labels = {phone:['Обнаружен телефон','high'],phone_raised:['Телефон поднят к экрану','high'],phone_aimed:['Возможное наведение телефона на экран','high'],phone_photo_attempt:['Признаки возможной попытки съёмки','high'],book:['Книга в кадре','medium'],additional_device:['Экран или ноутбук в кадре','medium'],paper_candidate:['Возможный лист с текстом','medium'],look_down:['Длительный взгляд вниз','medium'],look_left:['Длительный взгляд влево','medium'],look_right:['Длительный взгляд вправо','medium'],look_side:['Длительный взгляд в сторону','medium'],no_face:['Человек отсутствует в кадре','high'],multiple_faces:['В кадре несколько лиц','high'],low_light:['Недостаточное освещение','technical'],camera_lost:['Камера недоступна','technical'],focus_lost:['Окно теста потеряло фокус','medium'],tab_hidden:['Тест скрыт или переключена вкладка','medium'],fullscreen_exit:['Выход из полноэкранного режима','medium'],shortcut:['Попытка запрещённого действия','medium'],emergency_exit:['Аварийный выход из экзамена','technical']};
function summarize(events) {const pending=events.filter(e=>e.review==='pending');return {pending:pending.length,high:pending.filter(e=>e.severity==='high').length,medium:pending.filter(e=>e.severity==='medium').length,technical:pending.filter(e=>e.severity==='technical').length};}
function mockState() {let session=mock.active||((teacherToken||mock.visible)?mock.sessions[mock.sessions.length-1]:null)||null;if(session&&session.status==='running'){session.remaining=Math.max(0,Math.ceil(session.started+session.config.duration_minutes*60-Date.now()/1000));if(!session.remaining)mockFinish();}const events=teacherToken&&session?mock.events.filter(e=>e.session_id===session.id):[];return {viewer_role:teacherToken?'teacher':'participant',version:'1.9.7',participant:mock.participant?cloneData(mock.participant):null,demo:true,models_ready:false,camera:{running:mock.camera,camera_ok:mock.camera,calibrated:mock.calibrated,faces:mock.camera?1:0,fps:0,brightness:0,error:'',calibration_progress:mock.calibrated?100:0},guard:{desktop:false,supported:false,active:false,keyboard_hook:false,platform:'preview'},session:session?cloneData(session):null,events:cloneData(events),summary:summarize(events)};}
function mockFinish(status='completed'){if(!mock.active)return;let s=mock.active;let correct=demoQuestions.filter(q=>s.answers[q.id]===q.correct).length;s.result={correct,total:demoQuestions.length,percent:Math.round(correct/demoQuestions.length*100)};s.status=status;s.ended=Date.now()/1000;mock.active=null;return {...s.result,session_id:s.id,status};}
function mockReport(sid){const session=mock.sessions.find(s=>s.id===sid);const events=mock.events.filter(e=>e.session_id===sid);return {product:'Qorgau — интерфейсное демо',session,events,summary:summarize(events),integrity:{ok:null,root:'В интерфейсном демо криптографическая проверка не выполняется'},notice:'Искусственные события. Это демонстрация интерфейса, без камеры и блокировки ОС.'};}
async function mockApi(path, method, body){
 if(path==='/api/state')return mockState();
 if(path==='/api/heartbeat')return {ok:true};
 if(path==='/api/logout')return {ok:true};
 if(path==='/api/participant/reset'){if(mock.active)throw Error('Сначала завершите текущий тест');mock.visible=false;mock.participant=null;return {...mockState(),session:null,events:[],summary:summarize([]),viewer_role:'participant'};}
 if(path==='/api/participant/login'){if(mock.active)throw Error('Сначала завершите текущий тест');const name=String(body.name||'').trim(),group=String(body.group||'').trim();if(!name||!group)throw Error('Введите имя и группу');mock.participant={name,group};mock.visible=false;return mockState();}
 if(path==='/api/camera'){mock.camera=body.on;return {demo:true};}
 if(path==='/api/calibrate'){mock.calibrated=true;return {demo:true};}
 if(path==='/api/sessions'&&method==='POST'){if(mock.active)throw Error('Экзамен уже идёт');if(!mock.participant)throw Error('Сначала войдите как участник');if(!body.consent)throw Error('Подтвердите условия');const s={id:Math.random().toString(16).slice(2,10),name:mock.participant.name,group_name:mock.participant.group,mode:'demo',started:Date.now()/1000,ended:null,status:'running',config:body,answers:{},exam:demoQuestions.map(({correct,...q})=>q),result:null};mock.sessions.push(s);mock.active=s;mock.visible=true;return mockState();}
 if(path==='/api/answer'){if(!mock.active)throw Error('Нет активного экзамена');mock.active.answers[body.question_id]=body.option;return {answers:mock.active.answers};}
 if(path==='/api/finish')return mockFinish();
 if(path==='/api/emergency'){return mockFinish('interrupted');}
 if(path==='/api/demo-event'||path==='/api/client-event'){if(!mock.active)throw Error('Сначала начните демонстрационный экзамен');const [label,severity]=labels[body.kind]||['Событие','technical'];mock.events.push({id:mock.next++,session_id:mock.active.id,kind:body.kind,label,severity,detail:'Искусственное событие для демонстрации',duration:4,confidence:body.kind.includes('phone')?.91:null,offset:Math.round(Date.now()/1000-mock.active.started),at:Date.now()/1000,simulated:true,review:'pending',note:'',evidence:null});return mockState();}
 if(path==='/api/login'){if(body.pin!=='2026')throw Error('В интерфейсном демо PIN: 2026');return {token:'preview-token'};}
 if(!teacherToken)throw Error('Войдите как преподаватель');
 if(path==='/api/sessions')return mock.sessions.slice().reverse().map(s=>({...s,event_count:mock.events.filter(e=>e.session_id===s.id).length,summary:summarize(mock.events.filter(e=>e.session_id===s.id))}));
 if(path.match(/^\/api\/sessions\/[^/]+$/)){let sid=path.split('/')[3];if(method==='DELETE'){mock.sessions=mock.sessions.filter(s=>s.id!==sid);mock.events=mock.events.filter(e=>e.session_id!==sid);return {ok:true};}return mockReport(sid);}
 if(path.includes('/review')){let id=Number(path.split('/')[3]);let e=mock.events.find(e=>e.id===id);Object.assign(e,{review:body.decision,note:body.note});return {ok:true};}
 throw Error('Действие доступно в локальном приложении из архива');
}
async function api(path, method='GET', body, format='json', requestSignal){
 const requestToken=teacherToken, generation=authGeneration;
 if(preview){const payload=await mockApi(path,method,body);if(requestToken&&(requestToken!==teacherToken||generation!==authGeneration))throw Error('Кабинет изменён. Повторите действие.');return payload;}
 const response=await fetch(path,{method,headers:{'Content-Type':'application/json','X-Qorgau-Token':csrf,...(requestToken?{Authorization:'Bearer '+requestToken}:{})},body:body===undefined?undefined:JSON.stringify(body),signal:requestSignal});
 if(requestToken&&(requestToken!==teacherToken||generation!==authGeneration))throw Error('Кабинет изменён. Повторите действие.');
 if(!response.ok){
  let detail;try{detail=(await response.json()).detail;}catch{detail=await response.text().catch(()=>null);}
  if(response.status===401&&requestToken&&requestToken===teacherToken){clearTeacherMemory();role=isRunning()?'student':'choose';view=isRunning()?'exam':'choose';closeModal();history.replaceState(null,'','#'+view);if(state)render();}
  throw Error(typeof detail==='string'?detail:'Проверьте данные и повторите действие');
 }
 const payload=format==='blob'?await response.blob():await response.json();
 if(requestToken&&(requestToken!==teacherToken||generation!==authGeneration))throw Error('Кабинет изменён. Повторите действие.');
 return payload;
}
function toast(text){clearTimeout(toastTimer);$('#toast').textContent=text;$('#toast').classList.add('show');toastTimer=setTimeout(()=>$('#toast').classList.remove('show'),3600);}
function modal(content){$('#modal-content').innerHTML=content;$('#modal').showModal();setTimeout(()=>$('#modal input')?.focus(),80);}
function closeModal(){if(evidenceURL){URL.revokeObjectURL(evidenceURL);evidenceURL=null;}$('#modal').close();}
function modalHeader(title,desc=''){return `<div class="modal-header"><div><h2>${title}</h2>${desc?`<p>${desc}</p>`:''}</div><button type="button" class="close-btn" data-action="close-modal" aria-label="Закрыть">×</button></div>`;}
function updateTeacher(){
 const teacher=isTeacher();
 setText('teacher-label','Выйти');
 setText('teacher-sub',teacher?'Закрыть доступ преподавателя':'Завершить работу участника');
 $('#teacher-top')?.setAttribute('aria-label',teacher?'Выйти из кабинета преподавателя':'Сменить кабинет');
 $('#teacher-top')?.setAttribute('title',teacher?'Выйти из кабинета':'Сменить кабинет');
 setText('workspace-title',teacher?'Преподаватель':state?.participant?.name||'Участник');
 setText('workspace-sub',teacher?'Тесты и результаты':state?.participant?.group||'Подготовка и прохождение');
 setText('workspace-icon',teacher?'П':'У');
 setText('workspace-crumb',role==='choose'?'Qorgau':teacher?'Кабинет преподавателя':'Кабинет участника');
 setText('nav-label',teacher?'УПРАВЛЕНИЕ ЭКЗАМЕНОМ':'МОЙ ЭКЗАМЕН');
 setText('cabinet-badge',role==='choose'?'Локальный прокторинг':teacher?'Преподаватель':'Участник');
}
function isTeacher(){return role==='teacher'&&!!teacherToken;}
function clearTeacherMemory(){
 teacherToken='';authGeneration++;examDraft=null;examDirty=false;examError='';examLoading=false;editorSaving=false;
 sessions=[];selectedReport=null;reportsLoading=false;loginNext=null;eventFilter='all';
 if(state){state.events=[];state.summary=summarize([]);if(state.session?.status!=='running')state.session=null;}
}
function cabinetNav(){return role==='choose'?[]:isTeacher()?teacherNav:studentNav;}
function allowedView(next){
 if(examLocked())return 'exam';
 if(role==='choose')return ['choose','student-login','teacher-login'].includes(next)?next:'choose';
 if(role==='student'&&!state?.participant&&!isRunning())return 'student-login';
 return cabinetNav().some(([id])=>id===next)?next:'overview';
}
async function switchCabinet(target,discard=false){
 if(roleBusy)return;
 if(calibrationWizard)await endCalibrationWizard('cancelled','Калибровка отменена');
 if(isRunning()){toast('Сначала завершите текущий тест');return;}
 if(examDirty&&!discard){modal(`<div class="modal-body">${modalHeader('В тесте есть несохранённые изменения','Сохраните тест перед передачей компьютера или выйдите без этих изменений.')}<div class="button-row"><button class="btn secondary" data-action="close-modal">Остаться</button><button class="btn primary" data-action="confirm-cabinet" data-target="${['student','teacher'].includes(target)?target:'choose'}">Выйти без сохранения</button></div></div>`);return;}
 roleBusy=true;
 try{
  if(teacherToken)await api('/api/logout','POST',{});
  clearTeacherMemory();role='choose';view='choose';closeModal();stopStreams();drawNav();$('#main').innerHTML='<section class="panel empty-state" role="status">Открываем вход…</section>';
  state=await api('/api/participant/reset','POST',{});eventCount=0;lastStatus=(state.session?.id||'')+':'+(state.session?.status||'');
  role='choose';view=target==='student'?'student-login':target==='teacher'?'teacher-login':'choose';
  history.replaceState(null,'','#'+view);render();
 }catch(error){toast(error.message);render();}
 finally{roleBusy=false;}
}
function isRunning(){return state?.session?.status==='running';}
function examLocked(){return isRunning()&&!state.demo;}
function managedHub(){return !examSurface&&!!state?.guard?.isolation_available;}
function examInteractionGuard(){return examLocked()&&!managedHub();}
function pulseExamWindow(){
 if(!examSurface)return Promise.resolve();
 const bridge=window.pywebview?.api?.exam_heartbeat;
 return typeof bridge==='function'?Promise.resolve(bridge()).catch(()=>{}):Promise.resolve();
}
window.addEventListener('pywebviewready',()=>{pulseExamWindow();updateWindowControls();});
function updateWindowControls(){const controls=$('#window-controls');if(controls)controls.hidden=examSurface||isRunning()||!!state?.guard?.starting||typeof window.pywebview?.api?.hub_minimize!=='function';}
function guardDescription(){return state?.demo?'Демонстрация без блокировки':state?.guard?.isolation_available?'Включится только на время теста':state?.guard?.desktop_isolated?'Изолированный рабочий стол Windows':'Диагностика камеры — без изоляции';}
function guardStatus(){
 const g=state?.guard||{};
 if(state?.demo)return {label:'Демонстрация',detail:'Системная блокировка не включается'};
 if(!connected)return {label:'Нет связи',detail:'Состояние защиты не подтверждено'};
 if(isRunning()){
  const ok=g.desktop_isolated&&g.active&&g.keyboard_hook&&g.navigation_guard&&g.window_controller==='pyautogui';
  return ok?{label:'Защита активна',detail:'Клавиши, навигация и окно контролируются'}:{label:'Проверка защиты',detail:g.error||'Открывается защищённое окно теста'};
 }
 if(g.isolation_available)return {label:'При старте теста',detail:'Сейчас окно и клавиши работают без ограничений'};
 if(g.desktop_isolated)return {label:'Изоляция готова',detail:'Защита включается в начале экзамена'};
 return {label:'Диагностика',detail:'Для защищённого теста запустите 02_start.cmd'};
}
function timeString(seconds){seconds=Math.max(0,Math.floor(seconds||0));return `${Math.floor(seconds/60).toString().padStart(2,'0')}:${(seconds%60).toString().padStart(2,'0')}`;}
function dateString(t){return new Date(t*1000).toLocaleString('ru-RU',{day:'2-digit',month:'short',hour:'2-digit',minute:'2-digit'});}
function drawNav(){
 const items=examLocked()?studentNav.filter(([id])=>id==='exam'):cabinetNav();
 $('#nav').innerHTML=items.map(([id,title])=>`<a href="#${id}" title="${title}" data-view="${id}" ${id===view?'aria-current="page"':''} class="nav-item ${id===view?'active':''}">${icons[id]}<span>${title}</span>${id==='events'&&state?.events.length?`<span class="count">${state.events.length}</span>`:''}</a>`).join('');
 setText('crumb',view==='choose'?'Вход':view==='student-login'?'Вход участника':view==='teacher-login'?'Вход преподавателя':items.find(n=>n[0]===view)?.[1]||'Вход');
 document.body.classList.toggle('exam-locked',examLocked());document.body.dataset.view=view;document.body.dataset.role=role;
 $('#teacher-button').disabled=isRunning();if($('#teacher-top'))$('#teacher-top').disabled=isRunning();if($('#teacher-top'))$('#teacher-top').hidden=role==='choose';
 updateTeacher();updateWindowControls();
}
async function navigate(next){
 if(calibrationWizard)await endCalibrationWizard('cancelled','Калибровка отменена при переходе на другую страницу');
 const destination=allowedView(next);
 if(examLocked()&&next!=='exam')browserEvent('shortcut','Попытка открыть раздел '+String(next).slice(0,60));
 if(next!==destination)history.replaceState(null,'','#'+destination);
 view=destination;selectedReport=null;render();
 if(view==='reports'&&isTeacher())await loadReports();
 if(view==='settings'&&isTeacher()&&!examDraft)await loadExamConfig();
}
function heading(title,subtitle='',actions=''){return `<div class="page-heading"><div><div class="eyebrow">QORGAU / ${isRunning()?'ЭКЗАМЕН ИДЁТ':'ЛОКАЛЬНЫЙ ПРОКТОРИНГ'}</div><h1>${title}</h1><p class="subtitle">${subtitle}</p></div>${actions?`<div class="header-actions">${actions}</div>`:''}</div>`;}
function demoScene(){return `<svg class="scene" viewBox="0 0 640 380" fill="none" aria-hidden="true"><rect width="640" height="380" fill="#e7e7e7"/><path d="M0 0h205v380H0" fill="#f2f2f2"/><path d="M214 0v380M0 283h640" stroke="#d7d7d7"/><path d="M451 42h146v146H451z" fill="#fafafa" stroke="#dadada"/><path d="M464 54h55v122h-55zM533 54h51v122h-51z" fill="#ffffff"/><rect x="52" y="145" width="106" height="10" rx="3" fill="#c5c5c5"/><rect x="66" y="94" width="12" height="51" rx="2" fill="#bfbfbf"/><rect x="82" y="112" width="12" height="33" rx="2" fill="#bdbdbd"/><rect x="98" y="102" width="18" height="43" rx="2" fill="#d4d4d4"/><path d="M90 380c4-82 33-91 63-88 19 2 40 31 43 88" fill="#b3b3b3"/><path d="M160 380c4-118 42-171 157-171s153 54 159 171" fill="#707070"/><path d="M260 220c3 23 25 36 56 36 35 0 55-15 57-37l-25-22h-62z" fill="#b9b9b9"/><ellipse cx="317" cy="142" rx="66" ry="79" fill="#cfcfcf"/><path d="M253 144c-15-83 29-102 67-102 54 0 85 45 65 106l-11-37c-35 8-77-2-101-18z" fill="#353535"/><path d="M283 145h17m35 0h17" stroke="#626262" stroke-width="3" stroke-linecap="round"/><path d="M312 151v19h11m-24 16q18 11 36 0" stroke="#929292" stroke-width="2" stroke-linecap="round"/><path d="M284 223q33 25 66 0" stroke="#e6e6e6" stroke-width="3"/><path d="M268 65h-23v23m141 0V65h-23M245 196v23h23m95 0h23v-23" stroke="#ffffff" stroke-width="3"/><rect x="263" y="231" width="107" height="21" rx="5" fill="#242424"/><text x="316" y="245" text-anchor="middle" fill="#ffffff" font-size="9" font-family="Arial">ДЕМО · 1 ЛИЦО</text><path d="M101 333h437l-11 47H112z" fill="#d2d2d2"/><path d="M185 280h265a8 8 0 0 1 8 9l-12 91H190l-12-91a8 8 0 0 1 7-9" fill="#dedede"/><circle cx="317" cy="337" r="11" fill="#b8b8b8"/></svg>`;}
function examMeta(){return state?.exam_config||{title:'Основы баз данных и цифровых систем',duration_minutes:20,gaze_seconds:.25,question_count:8,configured:!!state?.demo};}
function locked(){return !state?.demo&&!!state?.enforcement?.blocked;}
function phoneRule(){return `первый распознанный кадр, оценка модели ≥ ${Math.round((state?.enforcement?.confidence??.40)*100)}%`; }
function calibrationCapabilities(source=state?.camera||{},persisted=false){
 const calibration=source.calibration||{};
 const mode=['full','partial','none'].includes(source.calibration_mode)?source.calibration_mode:source.calibrated?'full':'none';
 const saved=Math.max(0,Math.min(5,Number(source.calibration_saved_points??calibration.saved_points)||0));
 const total=Math.max(1,Number(source.calibration_total_points??calibration.total)||5);
 const viewport=calibration.viewport,now=calibrationViewport();
 const screenChanged=!persisted&&viewport&&['screen_width','screen_height','device_pixel_ratio'].some(key=>typeof viewport[key]==='number'&&Math.abs(viewport[key]-now[key])>(key==='device_pixel_ratio'?.001:2));
 const gaze=['calibrated','approximate','unavailable'].includes(source.gaze_monitoring)?source.gaze_monitoring:mode==='full'?'calibrated':'unavailable';
 return {mode:screenChanged&&mode==='full'?'partial':mode,saved,total,gaze:screenChanged?'unavailable':gaze,head:!screenChanged&&(source.head_calibrated===true||(source.head_calibrated===undefined&&mode==='full')),screenChanged:!!screenChanged};
}
function calibrationLabel(caps){return caps.mode==='full'?'Калибровка завершена':caps.saved?`Частично: ${caps.saved} из ${caps.total} точек`:'Калибровка не выполнена';}
function gazeCapabilityText(caps){return caps.gaze==='calibrated'?'Взгляд: контроль выхода за экран после подтверждения.':caps.gaze==='approximate'?'Взгляд: приблизительное наблюдение, без остановки теста по взгляду.':'Взгляд: не определяется; тест не останавливается по взгляду.';}
function monitoringText(source=state?.camera||{},persisted=false){if(persisted&&!Object.hasOwn(source,'calibration_mode'))return 'Параметры калибровки для этой попытки не сохранены.';const caps=calibrationCapabilities(source,persisted);return `${calibrationLabel(caps)}. ${gazeCapabilityText(caps)} ${caps.head?'Положение головы: контроль относительно сохранённой исходной позиции.':'Положение головы: исходная позиция не сохранена, автоматическая остановка отключена.'}${caps.screenChanged?' Экран или масштаб изменился: координаты взгляда не используются.':''}`;}
function strictRule(source=isRunning()?state.session.config:state?.camera||{},persisted=isRunning()){const caps=calibrationCapabilities(source,persisted);return `${gazeCapabilityText(caps)} Телефон — остановка сразу после распознавания.${caps.head?` Голова — при отклонении более ${state?.enforcement?.head_limit_deg??5}° от сохранённой позиции.`:' Остановка по голове отключена: нет исходной позиции.'}`;}

function terminationReason(session=state?.session){return session?.result?.termination_reason||(session===state?.session?state?.enforcement?.reason:null)||'unknown';}
function terminationLabel(session=state?.session){return {phone:'Обнаружен телефон',gaze:'Взгляд за пределами экрана',head:'Голова отклонена'}[terminationReason(session)]||'Сработал контроль экзамена';}
function phonePosePanel(id='phone-pose'){return `<div class="phone-pose" id="${id}" ${state.demo?'hidden':''}><span class="phone-pose-icon">${icons.phone}</span><div class="phone-pose-content"><strong data-pose-title>Телефон не обнаружен</strong><p data-pose-description>Поднятие и возможное наведение оцениваются по движению и положению телефона.</p><ol class="phone-stages" data-phone-stages aria-label="Наблюдаемые признаки телефона" hidden><li data-phone-stage="detected">В кадре</li><li data-phone-stage="raised">Поднят</li><li data-phone-stage="holding">У лица</li><li data-phone-stage="possible_photo">Возможная съёмка</li></ol><ul class="pose-reasons" data-pose-reasons hidden></ul><p class="pose-limit">Направление объектива и факт снимка не подтверждаются.</p></div></div>`;}
const objectLabels={book:'Книга',laptop:'Ноутбук в кадре',monitor:'Экран в кадре',paper_candidate:'Возможный лист с текстом'};
function observedObjectsPanel(id='observed-objects'){return `<div class="observed-objects" id="${id}" ${state.demo?'hidden':''}><div class="observed-heading"><strong>Предметы в кадре</strong><span data-objects-count>Ожидание</span></div><ul class="object-chips" data-object-list aria-label="Наблюдаемые предметы"></ul><p data-objects-empty>Включите камеру для наблюдения.</p><p class="objects-notice">Предметы отмечаются для проверки преподавателем. Распознанный телефон сразу останавливает тест.</p></div>`;}

function gazePanel(id='gaze-observation'){return `<div class="gaze-observation" id="${id}" ${state.demo?'hidden':''}><div class="observed-heading"><strong>${icons.eye} Взгляд и положение головы</strong><span data-face-presence>Ожидание камеры</span></div><div class="gaze-status"><strong data-gaze-direction>Не определяется</strong><span data-gaze-source>Сначала включите камеру</span></div><dl class="head-angles"><div><dt>Поворот головы</dt><dd data-head-yaw>—</dd></div><div><dt>Наклон головы</dt><dd data-head-pitch>—</dd></div><div><dt>Наклон вбок</dt><dd data-head-roll>—</dd></div></dl><p data-gaze-calibration>Калибровку можно завершить раньше или пропустить.</p><p class="gaze-notice" data-gaze-notice></p></div>`;}
function updateGaze(c){
 const fresh=!!c.camera_ok&&c.analysis_ready===true,oneFace=fresh&&c.faces===1;
 const caps=calibrationCapabilities(isRunning()?state.session.config:c,isRunning());
 const enabled=!isRunning()||state.session?.config?.gaze_enabled!==false;
 const directionNames={center:caps.gaze==='calibrated'?'На экране':'Примерно прямо',left:'Влево',right:'Вправо',down:'Вниз',up:'Вверх'};
 const valid=enabled&&oneFace&&caps.gaze!=='unavailable'&&c.eye_open!==false&&c.gaze_valid===true&&!!directionNames[c.gaze_direction];
 const reasons={uncalibrated:'Нет надёжной настройки взгляда',no_face:'Лицо не видно',multiple_faces:'В кадре несколько лиц',eyes_closed:'Глаза закрыты или недостаточно видны',low_light:'Недостаточное освещение',invalid_landmarks:'Ориентиры лица недостаточно видны',conflicting_cues:'Направления головы и глаз неоднозначны'};
 const sources={screen:'По калибровке экрана',calibrated_screen:'По калибровке экрана',head:'По положению головы',iris:'По положению радужек','head+iris':'По голове и радужкам',neutral:'В пределах нейтральной позиции'};
 const source=valid?(caps.gaze==='approximate'?'Приблизительно · только наблюдение':c.gaze_boundary_uncertain?'Погрешность не позволяет подтвердить выход за экран':sources[c.gaze_source]||'Оценка направления'):!enabled?'Контроль взгляда отключён для этой попытки':!c.camera_ok?'Включите камеру':!fresh?'Нет свежего результата анализа':c.faces===0?'Лицо не видно':c.faces>1?'В кадре несколько лиц':caps.gaze==='unavailable'?'Можно проходить тест без определения взгляда':c.eye_open===false?'Глаза закрыты или недостаточно видны':(reasons[c.gaze_reason]||'Недостаточно данных для оценки');
 const angle=value=>typeof value==='number'&&Number.isFinite(value)?`${value>0?'+':''}${Math.round(value)}°`:'—';
 const anglesAvailable=enabled&&oneFace&&caps.head&&c.head_pose_valid===true;
 for(const panel of document.querySelectorAll('#gaze-observation,#exam-gaze-observation')){
  panel.hidden=!!state.demo;
  panel.classList.toggle('attention',valid&&caps.gaze==='calibrated'&&c.gaze_outside_confirmable||fresh&&caps.head&&c.head_outside||fresh&&c.faces!==1);
  panel.classList.toggle('unavailable',!valid);
  $('[data-face-presence]',panel).textContent=!c.camera_ok?'Камера выключена':!fresh?'Ожидание анализа':c.faces===0?'Лицо не видно':c.faces===1?'1 лицо в кадре':`${c.faces} лица в кадре`;
  $('[data-gaze-direction]',panel).textContent=valid?(caps.gaze==='calibrated'&&c.gaze_boundary_uncertain?'Около границы экрана':caps.gaze==='calibrated'&&c.gaze_outside_confirmable?'За пределами экрана':directionNames[c.gaze_direction]):'Не определяется';
  $('[data-gaze-source]',panel).textContent=source;
  $('[data-head-yaw]',panel).textContent=anglesAvailable?angle(c.head_yaw):'—';
  $('[data-head-pitch]',panel).textContent=anglesAvailable?angle(c.head_pitch):'—';
  $('[data-head-roll]',panel).textContent=anglesAvailable?angle(c.head_roll):'—';
  $('[data-gaze-calibration]',panel).textContent=calibrationLabel(caps);
  $('[data-gaze-notice]',panel).textContent=gazeCapabilityText(caps);
 }
}

// Calibration uses a voluntary full-screen surface. It never enables the exam guard.
function calibrationViewport(){return {width:window.innerWidth,height:window.innerHeight,screen_width:window.screen.width,screen_height:window.screen.height,device_pixel_ratio:window.devicePixelRatio||1};}
function sameCalibrationViewport(a,b){return !!a&&!!b&&['width','height','screen_width','screen_height','device_pixel_ratio'].every(key=>Math.abs(a[key]-b[key])<1);}
async function settledCalibrationViewport(wizard){
 const deadline=Date.now()+2200;let previous=null,stableSince=Date.now();
 while(Date.now()<deadline){
  if(calibrationWizard!==wizard)return null;
  const current=calibrationViewport();if(!sameCalibrationViewport(previous,current))stableSince=Date.now();
  if(Date.now()-stableSince>=300){
   if(Math.abs(current.width-current.screen_width)>2||Math.abs(current.height-current.screen_height)>2)throw Error('Калибровка должна занимать весь экран. Верните масштаб страницы к 100% и повторите запуск через 02_start.cmd');
   return current;
  }
  previous=current;await new Promise(resolve=>setTimeout(resolve,60));
 }
 throw Error('Размер полноэкранного окна не стабилизировался. Повторите калибровку.');
}
function nextPaint(){return new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));}
async function calibrationRequest(path,body={}){
 const controller=new AbortController(),timer=setTimeout(()=>controller.abort(),10000);
 try{return await api(path,'POST',body,'json',controller.signal);}
 catch(error){throw Error(error.name==='AbortError'?'Приложение не отвечает. Повторите калибровку после подключения.':error.message);}
 finally{clearTimeout(timer);}
}
function calibrationNative(method){
 const fn=window.pywebview?.api?.[method];if(typeof fn!=='function')return Promise.resolve();
 // Keep begin/end in request order even when a native call completes late.
 const pending=nativeCalibrationQueue.then(async()=>{const result=await fn();if(result?.ok===false)throw Error(result.message||'Не удалось изменить размер окна для калибровки');return result;});
 nativeCalibrationQueue=pending.catch(()=>{});return pending;
}
function calibrationBlocker(camera,calibration={}){
 if(!camera.camera_ok)return 'camera_lost';
 if(!camera.analysis_ready)return 'stale_analysis';
 if(typeof calibration.issue==='string'){
  if(['','awaiting_target','settling','gathering','quality_retry','fit_retry','failed','cancelled','complete'].includes(calibration.issue))return '';
  return ({eyes_unavailable:'invalid_landmarks',camera_unavailable:'camera_lost',pose_unavailable:'invalid_pose'})[calibration.issue]||calibration.issue;
 }
 if(camera.faces===0)return 'no_face';
 if(camera.faces>1)return 'multiple_faces';
 if(Number.isFinite(camera.brightness)&&camera.brightness<30)return 'low_light';
 if(camera.eye_open===false)return 'eyes_closed';
 if(camera.landmarks_valid===false||camera.eye_features_valid===false)return 'invalid_landmarks';
 if(camera.pose_valid===false)return 'invalid_pose';
 return '';
}
function calibrationAdvice(reason){
 const advice={camera_lost:'Проверьте, что камера подключена и её не заняло другое приложение.',stale_analysis:'Анализ не успевает за видео. Закройте приложения, которые используют камеру или сильно нагружают компьютер.',slow_analysis:'Анализ не успевает за видео. Закройте приложения, которые используют камеру или сильно нагружают компьютер.',stale_frame:'Анализ не успевает за видео. Закройте приложения, которые используют камеру или сильно нагружают компьютер.',no_face:'Расположите лицо полностью в кадре и посмотрите на экран.',multiple_faces:'Для настройки в кадре должен оставаться один человек.',low_light:'Добавьте ровный свет перед лицом. Положение камеры и расстояние до неё менять не нужно.',eyes_closed:'Моргать можно. Если пауза не проходит, проверьте, что оба глаза видны и на очках нет бликов.',invalid_landmarks:'Камера не различает оба глаза. Уберите блики и проверьте, что лицо полностью в кадре.',invalid_pose:'Положение головы не определяется. Посмотрите прямо на экран и проверьте, что лицо полностью видно.',head_moved:'Верните голову в исходное положение. Между точками переводите только взгляд.',head_motion:'Верните голову в исходное положение. Между точками переводите только взгляд.',unstable:'Смотрите на чёрный центр точки. Оставьте камеру и голову неподвижными.',unstable_samples:'Смотрите на чёрный центр точки. Оставьте камеру и голову неподвижными.'};
 return advice[reason]||'Смотрите на чёрный центр точки, не на подсказку. Точка переместится сама; нажимать на неё не нужно.';
}
function calibrationSimpleHint(reason){
 const hints={camera_lost:'Подключите камеру и повторите настройку.',stale_analysis:'Камера обрабатывает изображение. Смотрите на точку.',slow_analysis:'Камера обрабатывает изображение. Смотрите на точку.',stale_frame:'Камера обрабатывает изображение. Смотрите на точку.',no_face:'Покажите лицо целиком и посмотрите на точку.',multiple_faces:'В кадре должен оставаться один человек.',low_light:'Добавьте немного света перед лицом.',eyes_closed:'Моргать можно. Держите оба глаза в кадре.',invalid_landmarks:'Проверьте, что оба глаза видны и на очках нет бликов.',invalid_pose:'Держите голову прямо и смотрите на точку.',head_moved:'Верните голову в исходное положение.',head_motion:'Верните голову в исходное положение.',unstable:'Смотрите на точку, оставляя голову неподвижной.',unstable_samples:'Смотрите на точку, оставляя голову неподвижной.'};
 return hints[reason]||'Смотрите на точку, не поворачивая голову.';
}
function calibrationFailureHint(calibration,blocker,message){
 const causes={fit_error:'Измерения разных точек не согласовались. Попробуйте ещё раз.',insufficient_eye_range:'Измерения точек получились слишком похожими. Попробуйте ещё раз.',degenerate_fit:'По этим измерениям не удалось различить направления взгляда. Попробуйте ещё раз.',validation_error:'Контрольная точка не подтвердила точность. Попробуйте ещё раз.',noise_error:'Не удалось подтвердить устойчивость измерений. Проверьте, что камера неподвижна.'};
 return causes[calibration?.issue]||(blocker?calibrationSimpleHint(blocker):message||'Попробуйте настроить взгляд ещё раз.');
}
function calibrationCheckpoint(calibration={}){
 const total=Number.isInteger(calibration.total)&&calibration.total>0?calibration.total:5;
 const points=Array.isArray(calibration.completed_points)?[...new Set(calibration.completed_points.filter(index=>Number.isInteger(index)&&index>=0&&index<total))]:Array.from({length:Math.max(0,Math.min(total,Number(calibration.saved_points)||Number(calibration.step)||0))},(_,index)=>index);
 return {total,points,saved:points.length};
}
function calibrationMetrics(object,keys){return Object.fromEntries(keys.filter(key=>(typeof object[key]==='number'&&Number.isFinite(object[key]))||typeof object[key]==='boolean').map(key=>[key,object[key]]));}
function calibrationQuality(quality){
 const source=quality&&typeof quality==='object'&&!Array.isArray(quality)?quality:{},finite=value=>typeof value==='number'&&Number.isFinite(value)&&value>=0;
 const clean=Object.fromEntries(['rms_error','max_fit_error','fit_rms_limit','fit_max_limit','validation_error','validation_limit','uncertainty','eye_range_x','eye_range_y','eye_noise_x','eye_noise_y','screen_jitter','screen_jitter_limit'].filter(key=>finite(source[key])).map(key=>[key,source[key]]));
 // Errors are scalar screen-normalized distances, never eye features or images.
 if(Array.isArray(source.point_errors)){const errors=source.point_errors.slice(0,12);if(errors.every(finite))clean.point_errors=errors;}
 return clean;
}
function calibrationQualityFailure(calibration,message=''){return ['fit_error','insufficient_eye_range','degenerate_fit','validation_error','noise_error'].includes(calibration?.issue)||/Слишком большая ошибка калибровки/.test(message);}
function calibrationQualityDetails(quality){
 const clean=calibrationQuality(quality),percent=value=>typeof value==='number'&&Number.isFinite(value*100)?`${(value*100).toFixed(4).replace(/0+$/,'').replace(/\.$/,'.0').replace('.',',')}%`:'—';
 const rows=[['Ошибка расчёта (RMS)','rms_error','fit_rms_limit'],['Максимальная ошибка расчёта','max_fit_error','fit_max_limit'],['Ошибка на проверочных точках','validation_error','validation_limit'],['Колебания оценки взгляда','screen_jitter','screen_jitter_limit'],['Итоговая погрешность','uncertainty','validation_limit']].filter(([,measured])=>Object.hasOwn(clean,measured));
 if(!rows.length)return '';
 return `<div class="calibration-quality-details"><p>Погрешность относительно размеров экрана:</p><table><thead><tr><th>Показатель</th><th>Измерено</th><th>Допустимо</th></tr></thead><tbody>${rows.map(([label,measured,limit])=>`<tr><th scope="row">${label}</th><td>${percent(clean[measured])}</td><td>${percent(clean[limit])}</td></tr>`).join('')}</tbody></table></div>`;
}
function calibrationTechnicalSnapshot(camera={}){
 const calibration=camera.calibration||{},code=value=>typeof value==='string'&&/^[a-z_\d-]{0,60}$/.test(value)?value:null;
 return {calibration:{...calibrationMetrics(calibration,['step','index','total','fit_points','validation_points','revision','saved_points','accepted_samples','required_samples','sample_span','required_span','point_retry','fit_retry_round','fit_retry_limit','current_point_restarts','restart_count','last_restart_step','target_remaining','sample_window_seconds','observation_fps','last_frame_age_ms']),mode:calibration.mode==='simple_5'?'simple_5':null,phase:code(calibration.phase),issue:code(calibration.issue),retry_kind:['fit','validation'].includes(calibration.retry_kind)?calibration.retry_kind:null,last_restart_reason:code(calibration.last_restart_reason),completed_points:calibrationCheckpoint(calibration).points,quality:calibrationQuality(calibration.quality)},camera:calibrationMetrics(camera,['camera_ok','analysis_ready','analysis_fps','analysis_ms','last_analysis_age_ms','fps','width','height','faces','eye_open','landmarks_valid','eye_features_valid','pose_valid','brightness'])};
}
function calibrationDiagnostic(wizard,outcome,message){
 // Deliberate allowlist: no image, face landmarks, participant, PIN, tokens, paths or full app state.
 const stopReason=/Размер|масштаб/.test(message)?'viewport_changed':/перезапущена/.test(message)?'session_replaced':/Связь|свежих данных|не отвечает/.test(message)?'connection_lost':/Полноэкран/.test(message)?'fullscreen_changed':outcome==='cancelled'?'cancelled':'calibration_failed';
 return {product:'Qorgau',ui_version:'1.9.7',app_version:/^\d+\.\d+\.\d+$/.test(state?.version||'')?state.version:null,outcome,stop_reason:stopReason,viewport:wizard.viewport?calibrationMetrics(wizard.viewport,['width','height','screen_width','screen_height','device_pixel_ratio']):null,...calibrationTechnicalSnapshot({...wizard.lastCamera,calibration:wizard.lastCalibration}),history:(wizard.history||[]).slice(-90)};
}
function calibrationSurface(){
 const surface=document.createElement('section');surface.id='calibration-surface';surface.className='calibration-surface';surface.setAttribute('aria-label','Калибровка взгляда');
 surface.innerHTML=`<div class="calibration-top"><div><span class="eyebrow">НАСТРОЙКА ВЗГЛЯДА · 1.9.7</span><span data-calibration-count>Подготовка</span></div><div class="calibration-actions"><button class="btn primary small" data-action="continue-calibration">Продолжить с тем, что есть</button><button class="btn secondary small" data-action="cancel-calibration">Закрыть · Esc</button></div></div><div class="calibration-target" data-calibration-target hidden aria-label="Смотрите на эту точку"><i></i></div><div class="calibration-coach"><p data-calibration-message role="status" aria-live="polite">Смотрите на точку, не поворачивая голову.</p><div class="calibration-checkpoints" data-calibration-checkpoints role="progressbar" aria-label="Сохранённые точки" aria-valuemin="0" aria-valuemax="5" aria-valuenow="0"></div></div>`;
 document.body.append(surface);document.body.classList.add('calibrating-screen');return surface;
}
async function startCalibrationWizard(){
 if(calibrationFinishing)await calibrationFinishing;
 if(calibrationWizard||isRunning()||examSurface)return;
 closeModal();
 if(state?.demo){await api('/api/calibrate','POST',{});demoCalibrated=true;await refresh();toast('Демонстрация: настоящая калибровка требует локальной камеры');return;}
 const camera=state?.camera;if(!camera?.camera_ok||!camera.analysis_ready)throw Error('Сначала включите камеру и дождитесь анализа изображения');
 if(camera.faces!==1)throw Error('Для калибровки в кадре должен быть один человек');
 const wizard={id:++calibrationSerial,surface:calibrationSurface(),step:null,ackStep:null,viewport:null,serverId:null,serverStarted:false,ending:false,lastUpdate:Date.now(),startedAt:Date.now(),history:[]};calibrationWizard=wizard;
 try{
  if(typeof wizard.surface.requestFullscreen!=='function')throw Error('Полноэкранная калибровка недоступна. Запустите Qorgau через 02_start.cmd');
  // Request while the calibration button's user activation is still live.
  await wizard.surface.requestFullscreen();
  if(calibrationWizard!==wizard){if(document.fullscreenElement===wizard.surface)await document.exitFullscreen().catch(()=>{});return;}
  await calibrationNative('hub_calibration_begin');
  if(calibrationWizard!==wizard)return;
  await nextPaint();
  const viewport=await settledCalibrationViewport(wizard);
  if(calibrationWizard!==wizard||!viewport)return;
  if(document.fullscreenElement!==wizard.surface)throw Error('Полноэкранный режим закрыт. Повторите калибровку');
  wizard.viewport=viewport;
  wizard.watchdog=setInterval(()=>{
   if(calibrationWizard!==wizard)return;
   calibrationNative('hub_calibration_ping').catch(error=>{if(calibrationWizard===wizard)endCalibrationWizard('failed',error.message);});
   if(Date.now()-wizard.lastUpdate>12000)endCalibrationWizard('failed','Нет свежих данных приложения. Калибровка остановлена.');
  },2500);
  wizard.startPromise=calibrationRequest('/api/calibrate').then(started=>{wizard.serverId=started.camera?.calibration?.id;return started;});
  const started=await wizard.startPromise;
  wizard.serverId=started.camera?.calibration?.id;
  if(typeof wizard.serverId!=='string'||!wizard.serverId)throw Error('Приложение не подтвердило новую калибровку. Обновите файлы проекта и повторите.');
  if(calibrationWizard!==wizard)return;
  wizard.serverStarted=true;wizard.lastUpdate=Date.now();await refresh();
 }catch(error){if(calibrationWizard===wizard)await endCalibrationWizard('failed',error.message);}
}
function endCalibrationWizard(outcome,message=''){
 const wizard=calibrationWizard;if(!wizard||wizard.ending)return calibrationFinishing||Promise.resolve();
 wizard.ending=true;calibrationWizard=null;clearInterval(wizard.watchdog);
 const generation=authGeneration;
 lastCalibrationDiagnostic=outcome==='complete'?null:calibrationDiagnostic(wizard,outcome,message);
 document.querySelectorAll('[data-action="download-calibration-diagnostics"]').forEach(button=>button.hidden=!lastCalibrationDiagnostic);
 // Restore the desktop immediately. Finalize the matching server attempt before allowing exam start.
 document.body.classList.remove('calibrating-screen');wizard.surface.remove();
 const exit=document.fullscreenElement?document.exitFullscreen().catch(()=>{}):Promise.resolve();
 const restore=calibrationNative('hub_calibration_end').catch(error=>toast(error.message));
 const finish=(async()=>{
  await Promise.allSettled([exit,restore]);
  let syncError=null;
  try{
   if(wizard.startPromise)await wizard.startPromise;
   if(outcome!=='complete'&&wizard.serverId)await calibrationRequest('/api/calibration/cancel',{calibration_id:wizard.serverId});
   const next=await api('/api/state');
   if(generation===authGeneration){state=next;updateDynamic();}
  }catch(error){syncError=error;if(generation===authGeneration)toast('Не удалось обновить состояние. Проверьте подключение перед началом теста.');}
  if(generation!==authGeneration)return;
  if(outcome==='failed'){
   const last=wizard.lastCalibration,checkpoint=calibrationCheckpoint(last),summary=last?`Версия 1.9.7 · сохранено ${checkpoint.saved} из ${checkpoint.total} точек.`:'Версия 1.9.7';
   const qualityFailure=calibrationQualityFailure(last,message),cause=calibrationFailureHint(last,wizard.lastBlocker,message);
   modal(`<div class="modal-body">${modalHeader('Можно перейти к экзамену','Сохранённые точки будут использованы. Неполная настройка не мешает начать тест.')}<p class="attention-text">${esc(monitoringText())}</p><details class="calibration-technical"><summary>Технические подробности</summary><p class="calibration-failure-summary">${esc(summary)}</p><p class="calibration-failure-summary">${esc(cause)}</p><p class="calibration-failure-summary">${esc(message)}</p>${qualityFailure?calibrationQualityDetails(last?.quality):''}<button class="btn secondary small" data-action="download-calibration-diagnostics">Сохранить диагностику</button></details><div class="button-row"><button class="btn secondary" data-action="retry-calibration">Повторить настройку</button><button class="btn primary" data-action="start">Перейти к экзамену</button></div></div>`);
  }
  else if(outcome==='complete')toast('Настройка завершена.');
  else if(outcome!=='continue'&&message)toast(message);
  if(syncError&&outcome==='continue')throw syncError;
 })();
 calibrationFinishing=finish;
 finish.finally(()=>{if(calibrationFinishing===finish)calibrationFinishing=null;}).catch(()=>{});
 return finish;
}
async function continueCalibration(){await endCalibrationWizard('continue');await startDialog();}

function updateCalibrationWizard(camera){
 const wizard=calibrationWizard;if(!wizard||!wizard.serverStarted)return;
 wizard.lastUpdate=Date.now();
 if(!sameCalibrationViewport(wizard.viewport,calibrationViewport())){endCalibrationWizard('failed','Размер экрана изменился. Повторите калибровку в текущем размере.');return;}
 const calibration=camera.calibration;
 if(!calibration)return;
 if(calibration.id!==wizard.serverId){endCalibrationWizard('failed','Калибровка была перезапущена. Повторите настройку.');return;}
 // A delayed response must never move a displayed target back to an older revision.
 if(Number.isInteger(calibration.revision)&&Number.isInteger(wizard.lastRevision)&&calibration.revision<wizard.lastRevision)return;
 if(Number.isInteger(calibration.revision))wizard.lastRevision=calibration.revision;
 wizard.lastCalibration=calibration;
 wizard.lastCamera=camera;
 (wizard.history||(wizard.history=[])).push({elapsed_ms:Math.max(0,Date.now()-(wizard.startedAt||Date.now())),...calibrationTechnicalSnapshot(camera)});if(wizard.history.length>90)wizard.history.shift();
 const observedBlocker=calibrationBlocker(camera,calibration);
 // A blink or one noisy frame must not alternate corrective instructions.
 if(observedBlocker!==wizard.pendingBlocker){wizard.pendingBlocker=observedBlocker;wizard.blockerSince=Date.now();}
 const blocker=observedBlocker&&Date.now()-wizard.blockerSince>=700?observedBlocker:'';
 if(blocker||!observedBlocker)wizard.lastBlocker=blocker;
 if(calibration.phase==='complete'){
  if(camera.calibrated)endCalibrationWizard('complete');else endCalibrationWizard('failed','Проверка калибровки не подтверждена.');return;
 }
 if(['failed','cancelled'].includes(calibration.phase)){endCalibrationWizard('failed',calibration.message||'Не удалось получить надёжную калибровку.');return;}
 if(!calibration.active)return;
 const target=calibration.target,step=calibration.step,pointKey=`${step}:${calibration.point_retry||0}`;
 if(!target||!Number.isInteger(step)||![target.x,target.y].every(value=>Number.isFinite(value)&&value>=0&&value<=1)){endCalibrationWizard('failed','Приложение передало некорректную точку калибровки.');return;}
 const surface=wizard.surface,point=$('[data-calibration-target]',surface),checkpoint=calibrationCheckpoint(calibration);
 $('[data-calibration-count]',surface).textContent=`Точка ${calibration.index??step+1} / ${checkpoint.total}`;
 $('[data-calibration-message]',surface).textContent=calibrationSimpleHint(blocker);
 surface.dataset.issue=blocker;surface.classList.toggle('calibration-paused',!!blocker);
 const checkpoints=$('[data-calibration-checkpoints]',surface);checkpoints.style.setProperty('--calibration-points',String(checkpoint.total));checkpoints.setAttribute('aria-valuenow',String(checkpoint.saved));checkpoints.setAttribute('aria-valuemax',String(checkpoint.total));
 checkpoints.innerHTML=Array.from({length:checkpoint.total},(_,index)=>`<span class="${checkpoint.points.includes(index)?'saved ':''}${index===step?'current':''}" aria-label="Точка ${index+1}: ${checkpoint.points.includes(index)?'сохранена':index===step?'сейчас':'впереди'}">${checkpoint.points.includes(index)?'✓':index+1}</span>`).join('');
 surface.classList.toggle('target-at-top',target.y<.2);
 surface.classList.toggle('target-at-bottom',target.y>.8);
 surface.classList.toggle('target-at-center',Math.abs(target.x-.5)<.13&&Math.abs(target.y-.5)<.13);
 surface.style.setProperty('--calibration-target-y',`${target.y*100}%`);
 if(wizard.pointKey===pointKey)return;
 wizard.step=step;wizard.pointKey=pointKey;wizard.ackStep=null;point.hidden=false;point.style.left=(target.x*100)+'%';point.style.top=(target.y*100)+'%';point.dataset.step=String(step);point.dataset.retry=String(calibration.point_retry||0);
 // No camera samples are labelled until the target has actually been painted.
 nextPaint().then(async()=>{
  if(calibrationWizard!==wizard||wizard.pointKey!==pointKey)return;
  if(document.fullscreenElement!==surface||!sameCalibrationViewport(wizard.viewport,calibrationViewport())){await endCalibrationWizard('failed','Полноэкранный режим изменился. Повторите калибровку.');return;}
  try{await calibrationRequest('/api/calibration/target',{step,viewport:wizard.viewport,calibration_id:wizard.serverId});if(calibrationWizard===wizard&&wizard.pointKey===pointKey)wizard.ackStep=step;}
  catch(error){if(calibrationWizard===wizard)await endCalibrationWizard('failed',error.message);}
 });
}
window.addEventListener('resize',()=>{const wizard=calibrationWizard;if(wizard?.viewport&&!sameCalibrationViewport(wizard.viewport,calibrationViewport()))endCalibrationWizard('failed','Размер экрана изменился. Повторите калибровку.');});
document.addEventListener('fullscreenchange',()=>{const wizard=calibrationWizard;if(wizard&&document.fullscreenElement!==wizard.surface)endCalibrationWizard('cancelled','Калибровка отменена');});
document.addEventListener('keydown',event=>{if(calibrationWizard&&event.key==='Escape')endCalibrationWizard('cancelled','Калибровка отменена');});
document.addEventListener('visibilitychange',()=>{if(calibrationWizard&&document.hidden)endCalibrationWizard('cancelled','Калибровка отменена: окно скрыто');});

function cameraPanel(){return `<section class="panel"><div class="panel-head"><h2>Камера и окружение</h2><span class="status-tag" id="camera-tag">${state.demo?'Демо-изображение':'Подготовка'}</span></div><div class="camera-wrap"><div class="camera-placeholder" id="camera-placeholder">${state.demo?demoScene():`<div class="camera-off">${icons.camera}<strong>Камера выключена</strong><span>Нажмите «Камера», чтобы увидеть изображение</span></div>`}</div><img id="camera-image" class="camera-img hidden" alt="Изображение с локальной камеры"><span class="camera-corner"><span class="dot"></span>${state.demo?'ДЕМОНСТРАЦИЯ':'ЛОКАЛЬНАЯ КАМЕРА'}</span><span class="camera-overlay" id="camera-fps">${state.demo?'Без видеозаписи':'Ожидание камеры'}</span><span class="camera-bottom-label" id="camera-label">${state.demo?'Иллюстрация · камера не используется':'Включите камеру для проверки'}</span></div><div class="camera-controls"><div class="camera-hint" id="calibration-hint"><b>${state.demo?'Демонстрационный режим':'Подготовка'}</b><br>${state.demo?'Предварительный просмотр без камеры':'Включите камеру. Калибровку можно пропустить'}</div><div class="button-row"><button class="btn secondary small" data-action="camera" ${isRunning()?'disabled':''}>${icons.camera} ${state.demo?'Проверка демо':'Камера'}</button><button class="btn ghost small" data-action="calibrate" ${isRunning()?'disabled':''}>${icons.eye} Калибровка</button><button class="btn ghost small" data-action="download-calibration-diagnostics" ${!lastCalibrationDiagnostic||state.demo?'hidden':''}>Диагностика калибровки</button></div></div><div id="camera-error" class="camera-error" hidden></div></section>`;}
function stats(){return `<div class="stats-grid"><article class="stat-card"><div class="stat-top">Состояние экзамена<span class="stat-icon">${icons.exam}</span></div><div class="stat-value stat-value-text" id="stat-status">${isRunning()?'В процессе':locked()?'Доступ приостановлен':state.session?'Завершён':examMeta().configured?'Готов к старту':'Нужна настройка'}</div><div class="stat-sub" id="stat-status-sub">${state.session?esc(state.session.name):'Проверка окружения перед началом'}</div></article><article class="stat-card"><div class="stat-top">Эпизоды для просмотра<span class="stat-icon">${icons.eye}</span></div><div class="stat-value" id="stat-pending">${state.summary.pending}</div><div class="stat-sub">Решение принимает <span>преподаватель</span></div></article><article class="stat-card"><div class="stat-top">Ответы сохранены<span class="stat-icon">${icons.shield}</span></div><div class="stat-value" id="stat-answers">${Object.keys(state.session?.answers||{}).length}<span class="stat-total"> / ${state.session?.exam.length||examMeta().question_count}</span></div><div class="stat-sub">Автоматически после каждого ответа</div></article><article class="stat-card"><div class="stat-top">Хранение данных<span class="stat-icon">${icons.reports}</span></div><div class="stat-value stat-value-text">${preview?'Демо':'Локально'}</div><div class="stat-sub">${preview?'Данные только в памяти этой вкладки':'Камера не передаётся в облако'}</div></article></div>`;}
function sessionCard(){let s=state.session,m=examMeta();return `<section class="panel session-card"><div class="panel-head"><h2>${isRunning()?'Текущий экзамен':'Экзаменационная сессия'}</h2><span class="status-tag">${state.demo?'DEMO':'LIVE'}</span></div><div class="session-body"><div class="session-kicker">${state.demo?'ДЕМОНСТРАЦИЯ ИНТЕРФЕЙСА':'ЭКЗАМЕН ПРЕПОДАВАТЕЛЯ'}</div><h2 class="session-name">${esc(s?.status==='running'?(s.config.exam_title||m.title):m.title)}</h2><p class="session-desc">${locked()?'Попытка остановлена. Для нового запуска нужен преподаватель.':!m.configured?'Преподаватель должен подготовить вопросы и сохранить экзамен.':'Ответы, время и события сохраняются на этом компьютере.'}</p><div class="session-divider"></div><div class="detail-row"><span>${isRunning()?'Участник':'Формат'}</span><b>${isRunning()?esc(s.name):`${m.question_count} вопросов · один ответ`}</b></div><div class="detail-row"><span>${isRunning()?'Осталось времени':'Время на прохождение'}</span><b id="session-time">${isRunning()?timeString(s.remaining):`${m.duration_minutes} минут`}</b></div><div class="detail-row"><span>Контроль взгляда</span><b id="session-gaze-mode">${state.demo?'Искусственные события':'По доступным измерениям'}</b></div><button class="btn lime wide" data-action="${isRunning()?'go-exam':locked()?'show-stopped':!m.configured?'wait-exam':'start'}">${isRunning()?'Продолжить экзамен':locked()?'Причина остановки':!m.configured?'Тест пока не готов':state.demo?'Начать демонстрацию':'Начать экзамен'}<span class="arrow-long">↗</span></button><div class="session-foot">${state.demo?'Камера в деморежиме не используется':`Строгий контроль. Повторный вход — с разрешения преподавателя.`}</div></div></section>`;}
function checksPanel(){return `<section class="panel"><div class="panel-head"><h2>Готовность системы</h2>${icons.shield}</div><div class="checks"><div class="check"><span class="check-circle">1</span><div><strong>Камера</strong><small id="check-camera-desc">${state.demo?'Не нужна для демонстрации':'Проверка доступа и изображения'}</small></div><span class="check-state" id="check-camera">${state.demo?'Демо':'Ожидание'}</span></div><div class="check"><span class="check-circle">2</span><div><strong>Калибровка взгляда</strong><small id="check-gaze-desc">Можно настроить полностью, частично или пропустить</small></div><span class="check-state" id="check-gaze">${state.demo?'Демо':'Ожидание'}</span></div><div class="check"><span class="check-circle">3</span><div><strong>Защита окружения</strong><small id="check-guard-desc">${guardDescription()}</small></div><span class="check-state" id="check-guard">${state.guard.desktop_isolated?'Изоляция':state.demo?'Демо':'Диагностика'}</span></div></div></section>`;}
function eventTable(events,full=false){if(!events.length)return `<div class="table-empty">${icons.shield}Пока нет событий для просмотра.<br>Они появятся здесь во время экзамена.</div>`;return `<div class="table-scroll"><table class="events-table"><thead><tr><th>От начала</th><th>Событие</th>${full?'<th class="hide-mobile">Подробности</th>':''}<th>Решение</th>${full?'<th>Действия</th>':''}</tr></thead><tbody>${events.slice().reverse().map(e=>`<tr><td style="font-variant-numeric:tabular-nums;white-space:nowrap">${timeString(e.offset)}</td><td><div class="event-title"><span class="event-dot ${e.severity}"></span>${esc(e.label)}</div>${e.simulated?'<small class="muted">искусственное событие</small>':''}</td>${full?`<td class="hide-mobile">${e.duration?`${e.duration} с · `:''}${e.confidence!=null?`${Math.round(e.confidence*100)}% модель · `:''}${esc(e.detail)}${e.note?`<br><b>Комментарий:</b> ${esc(e.note)}`:''}</td>`:''}<td><span class="review-badge ${e.review}">${{pending:'Ожидает просмотра',confirmed:'Наблюдение подтверждено',dismissed:'Ложное срабатывание'}[e.review]||'Ожидает'}</span></td>${full?`<td><div class="review-actions">${teacherToken?`<button class="btn ghost small" data-action="review" data-id="${e.id}">Проверить</button>${e.evidence?`<button class="btn ghost small" data-action="evidence" data-id="${e.id}">Кадр</button>`:''}`:'<button class="text-link" data-action="teacher">Вход по PIN ↗</button>'}</div></td>`:''}</tr>`).join('')}</tbody></table></div>`;}

function roleChooser(){return `<section class="role-heading"><div class="eyebrow">QORGAU</div><h1>Вход в систему</h1><p class="subtitle">Выберите свой кабинет.</p></section><div class="role-grid"><section class="panel role-card"><div class="role-icon">${icons.exam}</div><h2>Участник</h2><p>Подготовка, прохождение теста и ваш результат.</p><button class="btn primary wide" data-action="enter-student">Вход участника <span aria-hidden="true">→</span></button></section><section class="panel role-card"><div class="role-icon">${icons.shield}</div><h2>Преподаватель</h2><p>Редактор теста, журнал событий и результаты.</p><button class="btn secondary wide" data-action="teacher">Вход преподавателя <span aria-hidden="true">→</span></button></section></div>`;}
function studentLoginPage(){return `<section class="auth-layout"><button class="auth-back" data-action="choose-role">← Выбор кабинета</button><form id="student-login-form" class="panel auth-panel"><div class="role-icon">${icons.exam}</div><div class="eyebrow">КАБИНЕТ УЧАСТНИКА</div><h1>Вход участника</h1><p class="auth-description">Укажите данные, которые будут записаны в результат теста.</p><label class="field"><span>Имя и фамилия</span><input name="name" autocomplete="name" maxlength="100" required placeholder="Ваше имя и фамилия"></label><label class="field"><span>Группа</span><input name="group" autocomplete="off" maxlength="60" required placeholder="Например, ИСУ-23-2"></label><p id="form-error" class="modal-error" role="alert"></p><button type="submit" class="btn primary wide">Войти и подготовиться <span aria-hidden="true">→</span></button></form></section>`;}
function teacherLoginPage(){return `<section class="auth-layout"><button class="auth-back" data-action="choose-role">← Выбор кабинета</button><form id="login-form" class="panel auth-panel"><div class="role-icon">${icons.shield}</div><div class="eyebrow">КАБИНЕТ ПРЕПОДАВАТЕЛЯ</div><h1>Вход преподавателя</h1><p class="auth-description">${preview?'PIN предварительного просмотра: 2026.':'Введите PIN из окна запуска приложения.'}</p><label class="field"><span>PIN преподавателя</span><input type="password" name="pin" inputmode="numeric" autocomplete="off" maxlength="100" required placeholder="Введите PIN"></label><p id="form-error" class="modal-error" role="alert"></p><button type="submit" class="btn primary wide">Войти в кабинет <span aria-hidden="true">→</span></button></form></section>`;}
async function submitStudentLogin(form){
 if(!form.reportValidity())return;
 const button=$('button[type=submit]',form);if(button.disabled)return;
 const generation=authGeneration,name=form.elements.name.value.trim(),group=form.elements.group.value.trim();
 if(!name||!group){$('#form-error',form).textContent='Введите имя и группу';return;}
 button.disabled=true;$('#form-error',form).textContent='';
 try{
  const next=await api('/api/participant/login','POST',{name,group});
  if(!form.isConnected||view!=='student-login'||generation!==authGeneration)return;
  state=next;role='student';lastStatus=(state.session?.id||'')+':'+(state.session?.status||'');
  history.replaceState(null,'','#'+(locked()?'exam':'overview'));await navigate(locked()?'exam':'overview');
 }catch(error){if(form.isConnected)$('#form-error',form).textContent=error.message;}
 finally{if(button.isConnected)button.disabled=false;}
}
async function submitTeacherLogin(form){
 if(!form.reportValidity())return;
 const button=$('button[type=submit]',form);if(button.disabled)return;
 const generation=authGeneration;button.disabled=true;$('#form-error',form).textContent='';
 try{
  const result=await api('/api/login','POST',{pin:form.elements.pin.value});
  if(!form.isConnected||view!=='teacher-login'||generation!==authGeneration)return;
  const destination=loginNext;teacherToken=result.token;authGeneration++;role='teacher';
  state=await api('/api/state');lastStatus=(state.session?.id||'')+':'+(state.session?.status||'');eventCount=state.events.length;
  history.replaceState(null,'','#'+(destination==='setup'?'settings':'overview'));
  await navigate(destination==='setup'?'settings':'overview');loginNext=null;
  if(destination==='unlock')unlockDialog();
 }catch(error){if(form.isConnected){if(teacherToken){clearTeacherMemory();role='choose';}$('#form-error',form).textContent=error.message;}}
 finally{if(button.isConnected)button.disabled=false;}
}
function studentOverview(){
 const m=examMeta();
 return heading('Подготовка к тесту','Включите камеру. Калибровку можно пройти частично или пропустить и сразу начать тест.')+(!m.configured?`<div class="help-note">${icons.clock}<span><b>Тест ещё не подготовлен.</b> Дождитесь, пока преподаватель сохранит вопросы.</span></div>`:'')+`<div class="main-grid"><div>${cameraPanel()}</div><div class="session-column">${sessionCard()}${checksPanel()}</div></div>`;
}
function teacherOverview(){
 const m=examMeta();
 return heading('Кабинет преподавателя','Управление тестом и результатами.',`<button class="btn primary" data-action="enter-student">Передать участнику →</button>`)+(locked()?`<div class="handoff-note">${icons.phone}<span>Новая попытка заблокирована после автоматической остановки. Проверьте причину и результат перед разблокировкой.</span><button class="btn secondary small" data-action="unlock">Разрешить попытку</button></div>`:'')+`<div class="teacher-dashboard-grid"><section class="panel setting-panel teacher-summary"><span class="status-tag">${m.configured?'Тест сохранён':'Нужна подготовка'}</span><h2>${esc(m.title)}</h2><p>${m.question_count} вопросов · ${m.duration_minutes} минут</p><div class="button-row"><a href="#settings" class="btn primary">Редактировать тест →</a></div></section><section class="panel setting-panel teacher-summary"><div class="role-icon">${icons.reports}</div><h2>Результаты участников</h2><p>Ответы, баллы и сохранённые отчёты.</p><a href="#reports" class="btn secondary">Открыть результаты →</a></section><section class="panel setting-panel teacher-summary"><div class="role-icon">${icons.events}</div><h2>Журнал событий</h2><p>${state.session?`${esc(state.session.name)} · ${state.summary.pending} эпизодов для проверки`:'События последней попытки. Предыдущие журналы доступны в результатах.'}</p><a href="#events" class="btn secondary">Проверить события →</a></section></div>`;
}
function overview(){return isTeacher()?teacherOverview():studentOverview();}

function examPage(){if(isRunning()&&managedHub())return heading('Тест открыт','Вы проходите тест в отдельном защищённом окне.')+'<section class="panel empty-state"><h2>Попытка идёт</h2><p>После завершения теста вы вернётесь сюда и увидите результат.</p></section>';let s=state.session;if(locked()||s?.status==='disqualified')return stoppedPage();if(!s)return heading('Ваш экзамен','Перед началом включите камеру. Калибровка — по желанию.')+`<section class="panel empty-state"><div class="empty-icon">${icons.exam}</div><h2>Начните новую сессию</h2><p>${examMeta().question_count} вопросов, автоматическое сохранение ответов и локальный журнал событий.</p><button class="btn primary" data-action="start">${icons.play} Подготовить экзамен</button></section>`;
 if(s.status!=='running'){const r=s.result||{correct:'—',total:s.exam?.length||examMeta().question_count};return heading('Ваш результат','Ответы сохранены на этом компьютере.')+`<section class="panel result-panel"><div class="result-circle">${r.correct}<small>/ ${r.total}</small></div><h2>${s.status==='interrupted'?'Экзамен прерван':'Тест завершён'}</h2><p>Это результат ответов на вопросы.<br>Преподаватель отдельно проверит журнал наблюдений.</p>${state.demo?'':`<p class="monitoring-summary">${esc(monitoringText(s.config||{},true))}</p>`}<div class="button-row"><a href="#overview" class="btn secondary">На главную</a><button class="btn primary" data-action="choose-role">Завершить работу</button></div></section>`;}
 currentQuestion=Math.min(currentQuestion,s.exam.length-1);const q=s.exam[currentQuestion];return heading('Сосредоточьтесь на вопросе.',esc(s.config.exam_title||examMeta().title),`<div class="exam-controls"><button class="btn secondary" data-action="finish">Завершить экзамен</button>${state.demo?'':'<button class="btn danger" data-action="emergency">Аварийный выход</button>'}</div><span class="header-in-session" id="mobile-timer">${icons.clock} ${timeString(s.remaining)}</span>`)+`<div class="exam-layout"><section class="panel question-panel"><div class="question-meta"><span>ВОПРОС ${String(currentQuestion+1).padStart(2,'0')} / ${String(s.exam.length).padStart(2,'0')}</span><span>Один правильный ответ</span></div><h2 class="question-text">${esc(q.text)}</h2><div class="options">${q.options.map((option,i)=>`<button class="option ${s.answers[q.id]===i?'selected':''}" data-action="answer" data-option="${i}"><span class="option-letter">${'ABCD'[i]}</span>${esc(option)}${s.answers[q.id]===i?'<span style="margin-left:auto">✓</span>':''}</button>`).join('')}</div><div class="question-footer"><button class="btn secondary" data-action="previous" ${currentQuestion===0?'disabled':''}>← Назад</button><span class="save-state">${icons.check} <span id="save-status">${s.answers[q.id]===undefined?'Выберите ответ':'Ответ сохранён'}</span></span><button class="btn primary" data-action="${currentQuestion===s.exam.length-1?'finish':'next'}">${currentQuestion===s.exam.length-1?'Завершить':'Следующий →'}</button></div></section><aside class="exam-sidebar"><section class="panel"><div class="panel-head"><h2>Осталось времени</h2>${icons.clock}</div><div class="timer" id="timer">${timeString(s.remaining)}</div><div class="timer-label">Ответы сохраняются автоматически</div><div class="progress-track"><div class="progress-fill" style="width:${Object.keys(s.answers).length/s.exam.length*100}%"></div></div><div class="question-grid">${s.exam.map((q,i)=>`<button class="qnum ${i===currentQuestion?'current':''} ${s.answers[q.id]!==undefined?'answered':''}" data-action="question" data-index="${i}">${i+1}</button>`).join('')}</div></section><section class="panel observation-panel"><div class="panel-head"><h2>Наблюдение</h2><span class="dot"></span></div><p class="exam-guard-status" data-exam-guard-status></p><div class="exam-mini-camera">${state.demo?'<span>ДЕМОНСТРАЦИЯ · БЕЗ КАМЕРЫ</span>':'<img id="mini-camera" class="live-stream" alt="Ваша локальная камера">'}</div><p class="attention-text" style="padding:0 20px 20px">${state.demo?'Предварительный просмотр: камера и защита ОС не используются.':strictRule()}</p></section></aside></div>`;
}

function stoppedPage(){const s=state.session,r=s?.result;return heading('Экзамен остановлен','Доступ к ответам закрыт. Попытка сохранена.')+`<section class="panel result-panel stopped-panel"><div class="stop-icon">${terminationReason(s)==='phone'?icons.phone:icons.eye}</div><span class="status-tag red">АВТОМАТИЧЕСКОЕ ОТСТРАНЕНИЕ</span><h2>${esc(terminationLabel(s))}</h2><p>Система автоматически остановила тест по результату анализа камеры. Продолжить эту попытку нельзя.${locked()?' Для нового запуска требуется разрешение преподавателя.':' Преподаватель разрешил новую попытку.'}</p><div class="stopped-facts"><span>Ответы сохранены</span><b>${r?`${r.correct} / ${r.total}`:'Доступны в отчёте'}</b><span>Причина завершения</span><b>${esc(terminationLabel(s))}</b></div><div class="button-row">${locked()?'<button class="btn primary" data-action="choose-role">Вернуться к выбору кабинета</button>':'<button class="btn primary" data-action="start">Подготовить новую попытку</button>'}</div><p class="stop-footnote">При ошибке распознавания преподаватель может отметить ложное срабатывание в журнале и разрешить новую попытку. Запись остановленной сессии остаётся в отчётах.</p></section>`;}
function eventsPage(){const filtered=state.events.filter(e=>eventFilter==='all'||(eventFilter==='pending'?e.review==='pending':e.severity===eventFilter));return heading('Журнал событий','Каждый эпизод можно проверить и прокомментировать.',teacherToken?'<span class="status-tag">Режим преподавателя</span>':'<button class="btn primary" data-action="teacher">Войти преподавателю ↗</button>')+`<div class="filters">${[['all','Все события'],['pending','Не проверены'],['high','Телефон и присутствие'],['medium','Взгляд и окружение'],['technical','Технические']].map(([id,title])=>`<button class="filter ${eventFilter===id?'active':''}" data-action="filter" data-filter="${id}">${title}</button>`).join('')}</div><section class="panel"><div class="panel-head"><h2>${state.session?esc(state.session.name):'Текущая сессия'}</h2><span class="muted">${filtered.length} событий</span></div><div id="full-events">${eventTable(filtered,true)}</div></section>`;}
function reportsPage(){if(!teacherToken)return heading('Отчёты преподавателя','Результаты экзаменов, решения по событиям и экспорт.')+`<section class="panel empty-state"><div class="empty-icon">${icons.shield}</div><h2>Доступ по PIN</h2><p>Преподаватель проверяет спорные эпизоды, оставляет комментарии и сохраняет отчёт. ${preview?'PIN для интерфейсной демонстрации: <b>2026</b>.':'PIN напечатан в окне запуска приложения.'}</p><button class="btn primary" data-action="teacher">Войти преподавателю</button></section>`;
 if(selectedReport){const r=selectedReport,s=r.session;return heading('Отчёт об экзамене',esc(s.name)+' · '+esc(s.group_name),'<button class="btn secondary" data-action="back-reports">← Все отчёты</button>')+`<section class="panel"><div class="report-detail-head"><div><h2>${esc(s.name)}</h2><p>${dateString(s.started)} · ${s.mode==='demo'?'Демонстрационные события':'Реальная камера'}<br>${s.status==='disqualified'?`<strong class=termination-text>Отстранён: ${esc(terminationLabel(s))}</strong><br>`:''}Результат: ${s.result?`${s.result.correct} / ${s.result.total}`:'Экзамен ещё не завершён'} · Эпизоды: ${r.events.length}<br>${preview?'Проверка цепочки доступна в приложении':`Целостность журнала: ${r.integrity.ok?'подтверждена':'ОШИБКА'}`}</p></div><div class="button-row"><button class="btn secondary small" data-action="export" data-format="json" data-id="${s.id}">JSON</button><button class="btn secondary small" data-action="export" data-format="csv" data-id="${s.id}">CSV</button><button class="btn primary small" data-action="export" data-format="html" data-id="${s.id}">${icons.download} Отчёт / печать</button></div></div>${s.mode==='demo'?'':`<p class="monitoring-summary report-monitoring">${esc(monitoringText(s.config||{},true))}</p>`}${eventTable(r.events,true)}</section><div class="help-note">${icons.help}<span>«Наблюдение подтверждено» означает подтверждение видимого эпизода. Решение о нарушении правил принимает преподаватель с учётом контекста.</span></div>`;}
 return heading('Отчёты преподавателя','Сессии хранятся на этом устройстве.',`<button class="btn secondary" data-action="refresh-reports">Обновить ↻</button>`)+(reportsLoading?'<section class="panel empty-state"><p>Загрузка отчётов…</p></section>':sessions.length?`<div class="report-grid">${sessions.map(s=>`<section class="panel report-card"><span class="status-tag ${s.mode==='demo'?'warm':''}">${s.mode==='demo'?'ДЕМОНСТРАЦИЯ':'ЛОКАЛЬНЫЙ ЭКЗАМЕН'}</span>${s.status==='disqualified'?`<div class=termination-text>Отстранён: ${esc(terminationLabel(s))}</div>`:''}<h3>${esc(s.name)}</h3><p>${esc(s.group_name)||'Без группы'}<br>${dateString(s.started)}</p><div class="report-score">${s.result?`${s.result.correct}<small> / ${s.result.total}</small>`:'В процессе'}</div><p>${s.event_count} событий · ${s.summary.pending} ожидают просмотра</p><div class="button-row"><button class="btn primary small" data-action="open-report" data-id="${s.id}">Открыть отчёт ↗</button><button class="btn ghost small" data-action="delete" data-id="${s.id}">Удалить</button></div></section>`).join('')}</div>`:'<section class="panel empty-state"><div class="empty-icon">'+icons.reports+'</div><h2>Здесь появятся ваши отчёты</h2><p>Пройдите первый экзамен, затем вернитесь в этот раздел.</p></section>');
}
function examEditor(){if(!teacherToken)return `<section class="panel setting-panel exam-editor"><h2>Подготовьте свой экзамен</h2><p>Добавьте вопросы, отметьте правильные ответы и задайте время. Только преподаватель видит ключи ответов и меняет экзамен.</p><button class="btn primary" data-action="setup-exam">Войти и настроить</button></section>`;if(examLoading)return '<section class="panel setting-panel exam-editor"><h2>Загрузка экзамена…</h2></section>';if(!examDraft)return `<section class="panel setting-panel exam-editor"><h2>Редактор экзамена</h2><p class="modal-error">${esc(examError)}</p><button class="btn secondary" data-action="load-exam">Загрузить вопросы</button></section>`;const d=examDraft;return `<section class="panel setting-panel exam-editor"><div class="editor-heading"><div><h2>Редактор экзамена</h2><p>${examMeta().configured?'Сохранённый экзамен доступен участникам. Изменения применятся к новым попыткам.':'Сейчас показан начальный набор вопросов. Отредактируйте его или сохраните, чтобы разрешить настоящий экзамен.'}</p></div><span class="status-tag ${examDirty?'warm':''}" id="draft-state">${examDirty?'Есть несохранённые изменения':examMeta().configured?'Изменения сохранены':'Нужно сохранить экзамен'}</span></div><form id="exam-editor-form"><fieldset ${isRunning()?'disabled':''}><div class="editor-meta"><label class="field"><span>Название экзамена</span><input data-exam-field="title" value="${esc(d.title)}" maxlength="160" required></label><label class="field"><span>Длительность, минут</span><input data-exam-field="duration_minutes" type="number" min="1" max="180" value="${d.duration_minutes}" required></label><div class="gaze-policy"><strong>Контроль по доступным измерениям</strong><p>Полная калибровка включает контроль взгляда за экраном. Частичная даёт только наблюдения. Без калибровки экзамен тоже доступен. Телефон контролируется всегда; голова — если сохранена исходная позиция.</p></div></div><div class="editor-questions">${d.questions.map((q,i)=>`<article class="editor-question"><div class="editor-question-head"><h3>Вопрос ${i+1}</h3><button type="button" class="btn danger small" data-action="remove-question" data-index="${i}" ${d.questions.length===1?'disabled':''}>Удалить</button></div><label class="field"><span>Текст вопроса</span><textarea data-question="${i}" data-exam-field="text" maxlength="2000" required>${esc(q.text)}</textarea></label><div class="editor-options">${q.options.map((v,j)=>`<div class="editor-option"><label class="correct-choice" title="Правильный ответ"><input type="radio" name="correct-${i}" data-question="${i}" data-exam-field="correct" value="${j}" ${q.correct===j?'checked':''} required><span>${'ABCD'[j]}</span></label><input aria-label="Вариант ${'ABCD'[j]} вопроса ${i+1}" data-question="${i}" data-option="${j}" data-exam-field="option" maxlength="1000" value="${esc(v)}" required></div>`).join('')}</div><small class="muted">Кружком отметьте один правильный ответ.</small></article>`).join('')}</div><div class="editor-tools"><button type="button" class="btn secondary" data-action="add-question">+ Добавить вопрос</button><button type="button" class="btn ghost" data-action="import-exam">Импорт JSON</button><button type="button" class="btn ghost" data-action="export-exam">Экспорт JSON</button><input id="exam-import" type="file" accept="application/json,.json" hidden></div><p class="modal-error" id="exam-save-error">${esc(examError)}</p><div class="editor-save"><span>${isRunning()?'Редактирование доступно после завершения текущей попытки.':'Передайте управление участнику после сохранения и выхода из режима преподавателя.'}</span><button type="button" class="btn primary" data-action="save-exam" ${editorSaving?'disabled':''}>${editorSaving?'Сохранение…':'Сохранить экзамен'}</button></div></fieldset></form></section>`;}
function settingsPage(){return heading('Редактор теста','Вопросы, длительность и условия контроля.')+`<div class="setting-grid">${examEditor()}<section class="panel setting-panel"><h2>Условия остановки теста</h2><div class="setting-row"><span>Телефон</span><b>${phoneRule()}</b></div><div class="setting-row"><span>Взгляд</span><b>Только при полной калибровке и подтверждении выхода</b></div><div class="setting-row"><span>Голова</span><b>Отклонение более ${state?.enforcement?.head_limit_deg??5}° при сохранённой исходной позиции</b></div><div class="setting-row"><span>Действие</span><b>По доступным для этой попытки измерениям</b></div><div class="setting-row"><span>Повторный запуск</span><b>С разрешения преподавателя</b></div><p style="margin-top:16px">Видимый фрагмент телефона учитывается, если модель его распознала. Веб-камера не гарантирует обнаружение любого фрагмента или точное измерение взгляда. Ответы и журнал сохраняются.</p>${locked()?'<button class="btn secondary" data-action="unlock">Разрешить новую попытку</button>':''}</section></div>`;}
function stopStreams(){document.querySelectorAll('#camera-image,#mini-camera').forEach(img=>{img.removeAttribute('src');delete img.dataset.streaming;});}
function render(){
 if(!state)return;
 if(examSurface&&!isRunning()){$('#main').innerHTML='<section class="panel empty-state" role="status"><h1>Возвращаемся к результату…</h1><p>Экзаменационное окно закрывается.</p></section>';$('#nav').innerHTML='';return;}
 if(role==='teacher'&&!teacherToken)role='choose';
 if(examLocked()){if(teacherToken){api('/api/logout','POST',{}).catch(()=>{});clearTeacherMemory();closeModal();}role='student';view='exam';history.replaceState(null,'','#exam');}
 view=allowedView(view);
 const oldStream=renderedView===view?document.querySelector('img[data-streaming]'):null;
 if(renderedView!==view)stopStreams();
 const pages={choose:roleChooser,overview,exam:examPage,events:eventsPage,reports:reportsPage,settings:settingsPage,'student-login':studentLoginPage,'teacher-login':teacherLoginPage};
 $('#main').innerHTML=pages[view]();
 if(oldStream){const replacement=document.getElementById(oldStream.id);if(replacement)replacement.replaceWith(oldStream);else oldStream.removeAttribute('src');}
 renderedView=view;drawNav();updateDynamic();
}
function setText(id,value){const node=$('#'+id);if(node)node.textContent=value;}
function updateDynamic(){if(!state)return;const c=state.camera;setText('stat-pending',state.summary.pending);if($('#stat-answers'))$('#stat-answers').innerHTML=`${Object.keys(state.session?.answers||{}).length}<span class="stat-total"> / ${state.session?.exam.length||examMeta().question_count}</span>`;setText('session-time',isRunning()?timeString(state.session.remaining):`${examMeta().duration_minutes} минут`);setText('timer',timeString(state.session?.remaining));setText('mobile-timer',timeString(state.session?.remaining));
 if(!state.demo){const caps=calibrationCapabilities(c),label=calibrationLabel(caps);setText('camera-tag',c.camera_ok?'Прямой эфир':c.running?'Подключение камеры':'Камера выключена');setText('camera-fps',c.camera_ok?`Видео ${Number(c.fps||0).toFixed(0)} FPS · анализ ${Number(c.analysis_fps||0).toFixed(1)} FPS`:'Ожидание камеры');setText('camera-label',c.camera_ok?`${c.width||'—'} × ${c.height||'—'} · ${c.analysis_ready?`${c.faces} лиц · ${label}`:'загрузка анализа'}`:'Нажмите «Камера» для проверки');setText('check-camera',c.camera_ok&&c.analysis_ready?'Готово':'Ожидание');setText('check-gaze',label);setText('check-gaze-desc',gazeCapabilityText(caps));setText('session-gaze-mode',caps.gaze==='calibrated'?'Контроль выхода за экран':caps.gaze==='approximate'?'Только наблюдение':'Не определяется');if($('#calibration-hint'))$('#calibration-hint').innerHTML=!c.camera_ok?'<b>Подготовка</b><br>Включите камеру для проверки':!c.analysis_ready?'<b>Ожидание анализа</b><br>Дождитесь свежих показаний камеры':`<b>${esc(label)}</b><br>Можно начинать тест. ${esc(gazeCapabilityText(caps))}`;if($('#camera-error')){$('#camera-error').textContent=c.error||'';$('#camera-error').hidden=!c.error;}
 for(const button of document.querySelectorAll('[data-action="start"]'))button.disabled=!c.camera_ok||!c.analysis_ready;

 document.querySelectorAll('#camera-image,#mini-camera').forEach(img=>{if(c.camera_ok){if(!img.dataset.streaming){img.dataset.streaming='1';img.src='/api/camera/stream';}img.classList.remove('hidden');}else{img.removeAttribute('src');delete img.dataset.streaming;img.classList.add('hidden');}});if($('#camera-placeholder'))$('#camera-placeholder').style.display=c.camera_ok?'none':'grid';}
 updateCalibrationWizard(c);updateGaze(c);updatePhonePose(c);updateObservedObjects(c);const protection=guardStatus();setText('check-guard-desc',protection.detail);setText('check-guard',protection.label);const examGuard=$('[data-exam-guard-status]');if(examGuard){examGuard.textContent=protection.label+' · '+protection.detail;examGuard.classList.toggle('guard-warning',!state.demo&&protection.label!=='Защита активна');}
}
function updatePhonePose(c){
 const ready=c.camera_ok&&c.analysis_ready===true;
 const details=(c.phone_photo_attempt?c.photo_attempt_details:null)||c.phone_aim_details||{},attempt=ready&&!!c.phone_photo_attempt,aimed=ready&&!!c.phone_aimed,raised=ready&&!!c.phone_raised,detected=ready&&!!c.phone;
 const stagesKnown=['detected','raised','holding','possible_photo'];
 const stage=!detected?'':attempt?'possible_photo':stagesKnown.includes(details.stage)?details.stage:raised?'raised':'detected';
 const reasonLabels={tracked_upward_motion:'Движение вверх',broad_plane_toward_webcam:'Видна широкая сторона телефона',stable_hold:'Устойчивое удержание',near_primary_face:'Рядом с лицом'};
 for(const id of ['phone-pose','exam-phone-pose']){
  const box=$('#'+id);if(!box)continue;
  box.classList.toggle('attention',attempt||aimed||raised);
  $('[data-pose-title]',box).textContent=attempt?'Признаки возможной попытки съёмки':aimed?'Возможное наведение на экран':raised?'Телефон поднят':detected?'Телефон обнаружен':!c.camera_ok?'Ожидание камеры':!ready?'Ожидание анализа':'Телефон не обнаружен';
  $('[data-pose-description]',box).textContent=attempt?'Зафиксирована последовательность признаков: телефон поднят, затем устойчиво удерживается рядом с лицом. Это возможная подготовка к съёмке экрана; требуется проверка преподавателем.':aimed?'Телефон удерживается устойчиво, его видимая поверхность обращена в сторону веб-камеры. Это не доказывает направление объектива или нажатие кнопки съёмки.':raised?'Отмечено движение телефона вверх. Само по себе оно не доказывает фотографирование экрана.':detected?'Телефон распознан. Во время теста первого подходящего кадра достаточно для остановки.':!ready?'Статус обновится после получения свежего результата анализа.':'Телефон не найден в текущем кадре. Поднятие и возможное наведение оцениваются по движению и положению телефона.';
  if(ready&&detected&&details.near_face&&!attempt){$('[data-pose-description]',box).textContent+=' Телефон находится рядом с лицом.';}
  const stages=$('[data-phone-stages]',box);stages.hidden=!detected;
  stages.dataset.stage=stage;
  for(const item of stages.children){const name=item.dataset.phoneStage;const active=name===stage;item.classList.toggle('current',active);item.classList.toggle('observed',name==='detected'?detected:name==='raised'?(raised||details.observed_raise_age_seconds!=null):name==='holding'?(stage==='holding'||attempt):attempt);if(active)item.setAttribute('aria-current','step');else item.removeAttribute('aria-current');}
  const reasons=$('[data-pose-reasons]',box);const observed=ready&&detected&&Array.isArray(details.reasons)?details.reasons.filter(reason=>Object.hasOwn(reasonLabels,reason)):[];reasons.hidden=!observed.length;reasons.innerHTML=[...new Set(observed)].map(reason=>`<li>${reasonLabels[reason]}</li>`).join('');
 }
}
function updateObservedObjects(c){
 const ready=c.camera_ok&&c.analysis_ready===true;
 const objects=ready&&Array.isArray(c.objects)?c.objects.filter(o=>o&&Object.hasOwn(objectLabels,o.kind)):[];
 const groups=new Map();
 for(const object of objects){const entry=groups.get(object.kind)||{kind:object.kind,count:0,confidence:null};entry.count++;if(typeof object.confidence==='number'&&Number.isFinite(object.confidence)&&object.confidence>=0&&object.confidence<=1)entry.confidence=Math.max(entry.confidence??0,object.confidence);groups.set(object.kind,entry);}
 for(const id of ['observed-objects','exam-observed-objects']){
  const panel=$('#'+id);if(!panel)continue;
  $('[data-objects-count]',panel).textContent=!c.camera_ok?'Камера выключена':!ready?'Анализ ожидается':objects.length?String(objects.length):'Не найдены';
  const empty=$('[data-objects-empty]',panel);empty.hidden=objects.length>0;empty.textContent=!c.camera_ok?'Включите камеру для наблюдения.':!ready?'Ожидается свежий результат анализа.':'Книги, ноутбуки, экраны и листы с текстом в текущем кадре не отмечены.';
  const markup=[...groups.values()].map(o=>`<li class="object-chip ${o.kind==='paper_candidate'?'estimated':''}"><span>${objectLabels[o.kind]}${o.count>1?` × ${o.count}`:''}</span><small>${o.kind==='paper_candidate'?'Визуальный признак, без чтения текста':o.confidence!==null?`Оценка модели ${Math.round(o.confidence*100)}%`:'Наблюдение модели'}</small></li>`).join('');
  const list=$('[data-object-list]',panel);if(list.dataset.markup!==markup){list.innerHTML=markup;list.dataset.markup=markup;}
 }
}

async function refresh(){if(polling||roleBusy)return;polling=true;const generation=authGeneration,calibrationGeneration=calibrationSerial,calibrationId=calibrationWizard?.serverId||null;try{const next=await api('/api/state');await api('/api/heartbeat','POST',{});await pulseExamWindow();if(generation!==authGeneration||calibrationGeneration!==calibrationSerial||calibrationId!==(calibrationWizard?.serverId||null))return;const status=(next.session?.id||'')+':'+(next.session?.status||'');const changed=status!==lastStatus;const newEvent=next.events.length!==eventCount;state=next;connected=true;$('#connection').textContent=preview?'Интерфейсное демо':'Локальное подключение';$('#connection').style.color='';if(changed){lastStatus=status;currentQuestion=0;if(state.session?.status==='disqualified'){closeModal();view='exam';history.replaceState(null,'','#exam');if(document.fullscreenElement)document.exitFullscreen().catch(()=>{});}if(role!=='choose'||isRunning())render();}else{updateDynamic();if(newEvent&&view==='overview'&&$('#recent-events'))$('#recent-events').innerHTML=eventTable(state.events.slice(-3));if(newEvent&&view==='events')render();}if(newEvent){eventCount=next.events.length;drawNav();}}catch(e){if(generation!==authGeneration||calibrationGeneration!==calibrationSerial||calibrationId!==(calibrationWizard?.serverId||null))return;connected=false;if(calibrationWizard)endCalibrationWizard('failed','Связь с приложением потеряна. Повторите калибровку после подключения.');$('#connection').textContent='Нет связи с приложением';$('#connection').style.color='#404040';if(state){state.camera={...state.camera,analysis_ready:false};updateDynamic();setText('camera-tag','Связь потеряна');}}finally{polling=false;}}
async function loadReports(){reportsLoading=true;render();try{sessions=await api('/api/sessions');}catch(e){toast(e.message);}finally{reportsLoading=false;if(view==='reports'&&!selectedReport)render();}}
async function loadExamConfig(){if(!teacherToken||examLoading)return;examLoading=true;examError='';if(view==='settings')render();try{examDraft=preview?{title:examMeta().title,duration_minutes:20,gaze_seconds:.25,questions:cloneData(demoQuestions),configured:true}:await api('/api/exam-config');examDirty=false;}catch(e){examError=e.message;}finally{examLoading=false;if(view==='settings')render();}}
function setDirty(){examDirty=true;setText('draft-state','Есть несохранённые изменения');$('#draft-state')?.classList.add('warm');}
function validateExam(d){if(!d||typeof d.title!=='string'||!d.title.trim()||d.title.length>160||!Number.isInteger(Number(d.duration_minutes))||Number(d.duration_minutes)<1||Number(d.duration_minutes)>180||!Array.isArray(d.questions)||!d.questions.length||d.questions.length>100)throw Error('Нужны название, время от 1 до 180 минут и от 1 до 100 вопросов.');const seen=new Set();return {title:d.title.trim(),duration_minutes:Number(d.duration_minutes),gaze_seconds:.25,questions:d.questions.map((q,i)=>{if(typeof q.text!=='string'||!q.text.trim()||q.text.length>2000||!Array.isArray(q.options)||q.options.length!==4||q.options.some(v=>typeof v!=='string'||!v.trim()||v.length>1000)||!Number.isInteger(q.correct)||q.correct<0||q.correct>3)throw Error(`Проверьте вопрос ${i+1}: текст, четыре варианта и один правильный ответ.`);const id=typeof q.id==='string'&&q.id&&!seen.has(q.id)?q.id:'q'+(i+1)+'_'+Math.random().toString(36).slice(2,8);seen.add(id);return {id,text:q.text.trim(),options:q.options.map(v=>v.trim()),correct:q.correct};})};}
async function saveExam(){if(editorSaving||!$('#exam-editor-form').reportValidity())return;editorSaving=true;const button=$('[data-action=save-exam]');if(button){button.disabled=true;button.textContent='Сохранение…';}examError='';try{const payload=validateExam(examDraft);if(preview){examDraft=payload;toast('В интерфейсном демо вопросы не меняют тест. Используйте локальное приложение.');}else{examDraft=await api('/api/exam-config','PUT',payload);state=await api('/api/state');toast('Экзамен сохранён. Участники могут начинать.');}examDirty=false;}catch(e){examError=e.message;}finally{editorSaving=false;render();}}
function importExamFile(file){const importToken=teacherToken,importGeneration=authGeneration;if(!file||!isTeacher())return;if(file.size>2*1024*1024){toast('Файл слишком большой. Максимум 2 МБ.');return;}file.text().then(raw=>{if(!isTeacher()||importToken!==teacherToken||importGeneration!==authGeneration)return;examDraft=validateExam(JSON.parse(raw));examError='';setDirty();render();toast('Вопросы загружены. Проверьте их и сохраните экзамен.');}).catch(e=>{if(isTeacher()&&importToken===teacherToken&&importGeneration===authGeneration)toast('Не удалось импортировать: '+e.message);});}
async function startDialog(){
 if(calibrationWizard)return continueCalibration();
 if(calibrationFinishing)await calibrationFinishing;
 if(role!=='student'||!state.participant){toast('Сначала войдите как участник');return;}
 if(!state.demo&&!state.guard.isolation_available&&!state.guard.desktop_isolated){modal(`<div class="modal-body">${modalHeader('Нужен изолированный запуск','Для экзамена запустите 02_start.cmd на компьютере Windows.')}<p class="attention-text">Аварийный выход: <b>Ctrl + Shift + Q</b>.</p><button class="btn primary" data-action="close-modal">Понятно</button></div>`);return;}
 if(locked()){view='exam';history.replaceState(null,'','#exam');render();return;}
 if(!state.demo&&!examMeta().configured){toast('Дождитесь, пока преподаватель подготовит тест');return;}
 if(!state.demo&&(!state.camera?.camera_ok||!state.camera?.analysis_ready)){toast('Включите камеру и дождитесь анализа изображения. Калибровку можно пропустить.');return;}
 const identity=state.participant,m=examMeta();
 modal(`<form class="modal-body" id="start-form">${modalHeader(state.demo?'Демонстрационный тест':'Начало теста',esc(m.title)+' · '+m.duration_minutes+' минут')}<div class="auth-summary"><strong>${esc(identity.name)}</strong><span>${esc(identity.group)}</span></div><label class="check-label"><input type="checkbox" name="snapshots"><span>Разрешаю сохранять отдельные снимки событий для проверки преподавателем.</span></label>${!state.demo?`<div class="phone-policy">${icons.phone}<span>${esc(monitoringText())}<br>Телефон: ${phoneRule()}. Контроль телефона и защита окружения включаются на время экзамена. Ответы сохраняются.</span></div>`:''}<label class="check-label"><input type="checkbox" name="consent" required><span>Согласен с локальной обработкой изображения, сохранением ответов и журнала событий. ${state.demo?'Это демонстрация без камеры и блокировки ОС.':''}</span></label><p class="modal-error" id="form-error" role="alert"></p><button class="btn primary wide" type="submit">${icons.play} Начать тест</button>${state.demo?'':'<p class="switch-line">Защита включится после начала теста и снимется при его завершении. Аварийный выход: Ctrl + Shift + Q.</p>'}</form>`);
 $('#start-form').addEventListener('submit',async event=>{
  event.preventDefault();const f=event.currentTarget,button=$('button[type=submit]',f);if(button.disabled||!f.reportValidity())return;button.disabled=true;button.textContent=state.demo?'Начинаем тест…':'Открываем защищённое окно…';
  try{
   state=await api('/api/sessions','POST',{name:identity.name,group:identity.group,consent:f.elements.consent.checked,snapshots:f.elements.snapshots.checked,duration_minutes:Number(m.duration_minutes),gaze_seconds:.25,gaze_enabled:true,calibration_viewport:calibrationViewport()});
   lastStatus=state.session.id+':running';currentQuestion=0;closeModal();history.replaceState(null,'','#exam');await navigate('exam');toast('Тест начался. Ответы сохраняются автоматически.');
  }catch(error){if(f.isConnected)$('#form-error',f).textContent=error.message;}
  finally{if(button.isConnected){button.disabled=false;button.textContent='Начать тест';}}
 });
}
async function loginDialog(next=null){
 if(isRunning()){toast('Вход преподавателя доступен после завершения теста');return;}
 if(isTeacher()){await navigate(next==='setup'?'settings':'overview');return;}
 await switchCabinet('teacher');loginNext=typeof next==='string'?next:null;
}
function unlockDialog(){if(!teacherToken){loginDialog('unlock');return;}modal(`<div class="modal-body">${modalHeader('Разрешить новую попытку?','Остановленный экзамен и его события останутся в отчётах. Участник сможет начать новый тест.')}<div class="button-row"><button class="btn secondary" data-action="close-modal">Отмена</button><button class="btn primary" data-action="confirm-unlock">Разрешить новый экзамен</button></div></div>`);}
function finishDialog(){let missing=state.session.exam.length-Object.keys(state.session.answers).length;modal(`<div class="modal-body">${modalHeader('Завершить экзамен?',missing?`Без ответа осталось вопросов: ${missing}. Ответы уже сохранены.`:'Все вопросы заполнены. Можно сохранить итоговый результат.')}<div class="button-row"><button class="btn secondary" data-action="close-modal">Продолжить тест</button><button class="btn primary" data-action="confirm-finish">Завершить и сохранить</button></div></div>`);}
function reviewDialog(id){const event=(selectedReport?.events||state.events).find(e=>e.id===Number(id));if(!event)return;modal(`<form class="modal-body" id="review-form">${modalHeader('Проверка эпизода',esc(event.label)+' · '+timeString(event.offset))}<p class="attention-text">Подтвердите наблюдение или отметьте ложное срабатывание. Это решение не изменяет баллы за тест.</p><label class="field"><span>Решение преподавателя</span><select name="decision"><option value="pending">Оставить на проверке</option><option value="confirmed">Наблюдение подтверждено</option><option value="dismissed">Ложное срабатывание</option></select></label><label class="field"><span>Комментарий</span><textarea name="note" maxlength="1000" placeholder="Например: студент посмотрел на часы, нарушения не было">${esc(event.note)}</textarea></label><p class="modal-error" id="form-error"></p><button type="submit" class="btn primary wide">Сохранить решение</button></form>`);$('#review-form').elements.decision.value=event.review;$('#review-form').addEventListener('submit',async e=>{e.preventDefault();try{await api(`/api/events/${id}/review`,'POST',{decision:e.currentTarget.elements.decision.value,note:e.currentTarget.elements.note.value});if(selectedReport)selectedReport=await api('/api/sessions/'+selectedReport.session.id);state=await api('/api/state');closeModal();render();toast('Решение сохранено в истории проверки');}catch(error){$('#form-error').textContent=error.message;}});}
function downloadBlob(blob,name){const url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download=name;document.body.append(a);a.click();a.remove();setTimeout(()=>URL.revokeObjectURL(url),10000);}
async function exportReport(sid,format){if(!isTeacher())throw Error('Войдите в кабинет преподавателя');if(preview){const r=mockReport(sid);let content,mime;if(format==='json'){content=JSON.stringify(r,null,2);mime='application/json';}else if(format==='csv'){const clean=x=>'"'+String(x??'').replace(/"/g,'""').replace(/^[=+@-]/,"'")+'"';content='\ufeffВремя,Событие,Решение,Комментарий\n'+r.events.map(e=>[timeString(e.offset),e.label,e.review,e.note].map(clean).join(',')).join('\n');mime='text/csv';}else{content=`<!doctype html><html lang="ru"><meta charset="utf-8"><title>Qorgau — демоотчёт</title><style>body{font:15px/1.8 Arial;margin:45px;color:#171717}table{border-collapse:collapse;width:100%}td,th{border-bottom:1px solid #ddd;padding:12px;text-align:left}h1{font-size:36px}</style><h1>Qorgau · демонстрационный отчёт</h1><p>${esc(r.session.name)} · ${esc(r.session.group_name)}</p><p>Искусственные события. Камера и блокировка ОС не использовались.</p><p>Результат: ${r.session.result?.correct??'—'} / 8. Оценка не зависит от наблюдений.</p><table><tr><th>Время</th><th>Событие</th><th>Решение</th><th>Комментарий</th></tr>${r.events.map(e=>`<tr><td>${timeString(e.offset)}</td><td>${esc(e.label)}</td><td>${esc(e.review)}</td><td>${esc(e.note)}</td></tr>`).join('')}</table></html>`;mime='text/html';}downloadBlob(new Blob([content],{type:mime+';charset=utf-8'}),`qorgau-demo-${sid}.${format}`);return;}const blob=await api(`/api/sessions/${sid}/export/${format}`,'GET',undefined,'blob');if(!isTeacher())return;downloadBlob(blob,`qorgau-${sid}.${format}`);toast(format==='html'?'Отчёт сохранён. Откройте его и выберите «Печать → PDF».':'Отчёт сохранён');}
async function action(name,el){
const teacherActions=['setup-exam','load-exam','add-question','remove-question','save-exam','import-exam','export-exam','confirm-unlock','review','refresh-reports','back-reports','open-report','export','delete','confirm-delete','evidence'];
if(teacherActions.includes(name)&&!isTeacher()){toast('Это действие доступно в кабинете преподавателя');return;}
if(examLocked()&&!['close-modal','go-exam','next','previous','question','answer','finish','confirm-finish','emergency'].includes(name)){browserEvent('shortcut','Недоступное действие во время экзамена: '+name);toast('Во время экзамена доступен только тест');return;}switch(name){
case 'minimize-window':case 'close-window':{if(isRunning()||examSurface)return;const fn=window.pywebview?.api?.[name==='minimize-window'?'hub_minimize':'hub_close'];if(typeof fn!=='function')return;const result=await fn();if(result?.ok===false)toast(result.message);break;}
case 'close-modal':closeModal();break;
case 'teacher':await loginDialog();break;
case 'enter-student':await switchCabinet('student');break;
case 'choose-role':await switchCabinet('choose');break;
case 'confirm-cabinet':await switchCabinet(el.dataset.target,true);break;
case 'wait-exam':toast('Дождитесь, пока преподаватель подготовит и сохранит тест');break;
case 'start':await startDialog();break;
case 'show-stopped':view='exam';location.hash='exam';render();break;
case 'setup-exam':view='settings';location.hash='settings';render();if(!teacherToken)loginDialog('setup');else if(!examDraft)await loadExamConfig();break;
case 'load-exam':await loadExamConfig();break;
case 'add-question':if(isRunning())return;if(examDraft.questions.length>=100)throw Error('Максимум 100 вопросов');examDraft.questions.push({id:'q'+Date.now(),text:'',options:['','','',''],correct:0});setDirty();render();break;
case 'remove-question':if(isRunning()||examDraft.questions.length<=1)return;examDraft.questions.splice(Number(el.dataset.index),1);setDirty();render();break;
case 'save-exam':if(!isRunning())await saveExam();break;
case 'import-exam':if(!isRunning())$('#exam-import').click();break;
case 'export-exam':downloadBlob(new Blob([JSON.stringify(validateExam(examDraft),null,2)],{type:'application/json;charset=utf-8'}),'qorgau-exam.json');break;
case 'unlock':unlockDialog();break;
case 'confirm-unlock':await api('/api/unlock','POST',{});closeModal();state=await api('/api/state');render();toast('Новая попытка разрешена. Предыдущий результат сохранён.');break;
case 'go-exam':location.hash='exam';break;
case 'camera':await api('/api/camera','POST',{on:true,index:0});demoCamera=true;toast(state.demo?'Демонстрация: реальная камера не включалась':'Камера запускается. Загрузка моделей может занять несколько секунд.');await refresh();break;
case 'calibrate':case 'retry-calibration':await startCalibrationWizard();break;
case 'cancel-calibration':await endCalibrationWizard('cancelled','Настройка закрыта. Сохранённые точки можно использовать в экзамене.');break;
case 'continue-calibration':await continueCalibration();break;
case 'download-calibration-diagnostics':if(lastCalibrationDiagnostic&&!isRunning())downloadBlob(new Blob([JSON.stringify(lastCalibrationDiagnostic,null,2)],{type:'application/json;charset=utf-8'}),'qorgau-calibration-diagnostic.json');break;
case 'next':currentQuestion++;render();break;
case 'previous':currentQuestion--;render();break;
case 'question':currentQuestion=Number(el.dataset.index);render();break;
case 'answer':{const q=state.session.exam[currentQuestion];const res=await api('/api/answer','POST',{question_id:q.id,option:Number(el.dataset.option)});state.session.answers=res.answers;render();break;}
case 'finish':finishDialog();break;
case 'emergency':await api('/api/emergency','POST',{});closeModal();if(document.fullscreenElement)await document.exitFullscreen().catch(()=>{});await refresh();toast('Экзамен прерван. Выполняется возврат на обычный рабочий стол.');break;
case 'confirm-finish':await api('/api/finish','POST',{});closeModal();if(document.fullscreenElement)await document.exitFullscreen().catch(()=>{});await refresh();render();toast('Ответы сохранены, экзамен завершён');break;
case 'filter':eventFilter=el.dataset.filter;render();break;
case 'review':if(!teacherToken){loginDialog();return;}reviewDialog(el.dataset.id);break;
case 'refresh-reports':await loadReports();break;
case 'back-reports':selectedReport=null;await loadReports();break;
case 'open-report':selectedReport=await api('/api/sessions/'+el.dataset.id);render();break;
case 'export':await exportReport(el.dataset.id,el.dataset.format);break;
case 'delete':modal(`<div class="modal-body">${modalHeader('Удалить сессию?', 'Будут удалены её ответы, журнал, решения преподавателя и сохранённые снимки.')}<div class="button-row"><button class="btn secondary" data-action="close-modal">Отмена</button><button class="btn danger" data-action="confirm-delete" data-id="${esc(el.dataset.id)}">Удалить сессию</button></div></div>`);break;
case 'confirm-delete':await api('/api/sessions/'+el.dataset.id,'DELETE');closeModal();state=await api('/api/state');await loadReports();toast('Сессия и её снимки удалены');break;
case 'evidence':{const blob=await api('/api/evidence/'+el.dataset.id,'GET',undefined,'blob');if(!isTeacher())return;evidenceURL=URL.createObjectURL(blob);modal(`<div class="modal-body">${modalHeader('Снимок эпизода','Локальный кадр, сохранённый с согласия участника.')}<img class="screenshot-view" src="${evidenceURL}" alt="Кадр события"></div>`);break;}
}}
document.addEventListener('input',e=>{const t=e.target;if(!t.dataset.examField||!examDraft||!teacherToken)return;const f=t.dataset.examField;const q=t.dataset.question;if(q!==undefined){if(f==='option')examDraft.questions[Number(q)].options[Number(t.dataset.option)]=t.value;else examDraft.questions[Number(q)][f]=f==='correct'?Number(t.value):t.value;}else examDraft[f]=['duration_minutes','gaze_seconds'].includes(f)?Number(t.value):t.value;setDirty();});
document.addEventListener('change',e=>{if(e.target.id==='exam-import')importExamFile(e.target.files?.[0]);});
document.addEventListener('submit',e=>{if(e.target.id==='student-login-form'){e.preventDefault();submitStudentLogin(e.target);}else if(e.target.id==='login-form'){e.preventDefault();submitTeacherLogin(e.target);}else if(e.target.id==='exam-editor-form'){e.preventDefault();saveExam();}});
document.addEventListener('click',e=>{const el=e.target.closest('[data-action]');if(el){e.preventDefault();if(el.disabled)return;Promise.resolve(action(el.dataset.action,el)).catch(error=>toast(error.message));}});
$('#teacher-button').addEventListener('click',()=>switchCabinet('choose'));
$('#modal').addEventListener('click',e=>{if(e.target===$('#modal'))closeModal();});
window.addEventListener('hashchange',()=>navigate(location.hash.slice(1)));
document.addEventListener('click',e=>{if(!examInteractionGuard())return;const a=e.target.closest('a[href]');if(!a)return;const href=a.getAttribute('href');if(href!=='#exam'||a.target==='_blank'||a.hasAttribute('download')||e.ctrlKey||e.metaKey||e.shiftKey){e.preventDefault();e.stopImmediatePropagation();browserEvent('shortcut','Попытка открыть другую страницу или вкладку');}},true);
document.addEventListener('auxclick',e=>{if(examInteractionGuard()&&e.target.closest('a[href]')){e.preventDefault();browserEvent('shortcut','Попытка открыть новую вкладку');}},true);
const normalWindowOpen=window.open.bind(window);window.open=function(...args){if(examInteractionGuard()){browserEvent('shortcut','Попытка открыть новое окно');return null;}return normalWindowOpen(...args);};
function browserEvent(kind,detail=''){if(isRunning()&&!state.demo&&!managedHub())api('/api/client-event','POST',{kind,detail}).catch(()=>{});}
document.addEventListener('visibilitychange',()=>{if(document.hidden)browserEvent('tab_hidden');});
window.addEventListener('blur',()=>browserEvent('focus_lost'));
document.addEventListener('fullscreenchange',()=>{if(!document.fullscreenElement)browserEvent('fullscreen_exit');});
for(const event of ['copy','paste','cut','contextmenu','dragstart'])document.addEventListener(event,e=>{if(isRunning()&&!state.demo&&!managedHub()){e.preventDefault();browserEvent('shortcut',event);}});
document.addEventListener('keydown',async e=>{if(!isRunning()||state.demo||managedHub())return;const k=/^(Key[A-Z]|Digit[0-9])$/.test(e.code)?e.code.replace(/^(Key|Digit)/,'').toLowerCase():e.key.toLowerCase();if(e.ctrlKey&&e.shiftKey&&k==='q'){e.preventDefault();try{await api('/api/emergency','POST',{});if(document.fullscreenElement)await document.exitFullscreen();await refresh();toast('Экзамен прерван, защита снята');}catch(error){toast(error.message);}return;}if(e.ctrlKey&&e.altKey&&k==='delete')return;const modifier=e.ctrlKey||e.metaKey;const blocked=modifier&&(['c','v','x','p','s','u','l','n','t','w','tab','pageup','pagedown','r','o','insert','escape'].includes(k)||/^[0-9]$/.test(k))||e.altKey&&['tab','escape','f4','arrowleft','arrowright'].includes(k)||e.shiftKey&&['insert','delete'].includes(k)||['meta','os','contextmenu','f5','f11','f12','printscreen'].includes(k);if(blocked){e.preventDefault();browserEvent('shortcut',[e.ctrlKey?'Ctrl':'',e.altKey?'Alt':'',e.shiftKey?'Shift':'',e.metaKey?'Win':'',e.key].filter(Boolean).join('+'));}});
window.addEventListener('pagehide',stopStreams);
window.addEventListener('beforeunload',e=>{if(examInteractionGuard()){e.preventDefault();e.returnValue='';}});
async function init(){if(examSurface){document.body.dataset.role='exam-window';$('#main').innerHTML='<section class="panel empty-state" role="status"><h1>Открываем тест…</h1><p>Подготавливаем вопросы и защищённое окно.</p><p>Аварийный выход: Ctrl + Shift + Q.</p></section>';$('#nav').innerHTML='';$('#teacher-top').hidden=true;}try{await pulseExamWindow();state=await api('/api/state');await api('/api/heartbeat','POST',{});await pulseExamWindow();const banner=$('#mode-banner');if(state.demo){banner.hidden=false;banner.innerHTML=`<strong>${preview?'ИНТЕРФЕЙСНОЕ ДЕМО':'ДЕМОНСТРАЦИЯ'}</strong> Предварительный просмотр без камеры и системной блокировки.${preview?' Для настоящего распознавания запустите приложение из архива.':''}`;}if(!state.demo&&!state.guard.isolation_available&&!state.guard.desktop_isolated){banner.hidden=false;banner.innerHTML='<strong>ДИАГНОСТИКА КАМЕРЫ</strong> Для изолированного экзамена запустите 02_start.cmd на компьютере Windows.';}lastStatus=(state.session?.id||'')+':'+(state.session?.status||'');const entry=['student-login','teacher-login'].includes(location.hash.slice(1))?location.hash.slice(1):null;role=isRunning()?'student':entry?'choose':state.participant?'student':'choose';await navigate(isRunning()?'exam':entry||(state.participant?'overview':'choose'));$('#preview-fallback-notice')?.remove();setInterval(refresh,1000);}catch(error){$('#main').innerHTML=`<section class="panel empty-state"><h2>Не удалось подключиться</h2><p>${esc(error.message)}<br>Запустите приложение командой из инструкции.</p></section>`;}}
init();
