INSERT INTO meetings(id,title,created,demo,duration,source_label) VALUES(lower(hex(randomblob(16))),'产品设计周会',datetime('now'),1,184,'示例会议');
INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES((SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'speech',5,26,'林悦','今天先一起看新版工作台的设计。我们重点确认两个问题：首次使用时，用户先看到什么，以及怎样把讨论中的结论留下来。');
INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES((SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'speech',31,55,'周航','我希望会议记录和 AI 对话能放在一起。讨论进行到一半时，可以直接追问，不用切出去找另一个聊天窗口。');
INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES((SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'speech',68,87,'林悦','可以，把需要跟进的问题放到右边的便签里。会议结束之后，也能快速回顾今天做的决定。');
INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES((SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'speech',118,141,'周航','那我来准备第一版原型。下次评审前，我们找两位同事试用一下，看看他们能不能顺利完成整个流程。');
INSERT INTO events(meeting,kind,at,end,speaker,text) VALUES((SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'assistant','93','93','听序 AI','右侧已记下双栏布局的共识。首次打开工作台时，用户先看记录还是先向 AI 提问？这个问题可以留到原型试用时观察。');
INSERT INTO notes VALUES(lower(hex(randomblob(16))),(SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'decision','保留双栏工作台','左侧记录会议与 AI 对话，右侧持续整理讨论结论。','听序 AI',datetime('now'));
INSERT INTO notes VALUES(lower(hex(randomblob(16))),(SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'task','准备第一版可交互原型','周航负责，在下次评审前邀请两位同事试用完整流程。','听序 AI',datetime('now'));
INSERT INTO notes VALUES(lower(hex(randomblob(16))),(SELECT id FROM meetings WHERE demo=1 ORDER BY rowid DESC LIMIT 1),'question','首次使用时应该先看到什么？','待原型试用后，根据同事的反馈确定默认视图。','林悦',datetime('now'));
