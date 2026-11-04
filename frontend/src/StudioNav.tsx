import type { ReactNode } from "react";
import {
  AudioLines, Boxes, Captions, Contact, History, Menu, NotebookPen, Palette,
  Plus, SlidersHorizontal, Users,
} from "lucide-react";
import { Modal } from "./components";
import { version as uiVersion } from "../package.json";
const mark = new URL("./brand.svg", import.meta.url).href;

const groups = [
  { label: "工作空间", pages: [
    { id: "studio", label: "工作台", icon: AudioLines, caption: "RECORDING DESK" },
    { id: "characters", label: "角色卡与读音", icon: Contact, caption: "CHARACTERS" },
    { id: "voices", label: "参考音色", icon: Users, caption: "VOICE LIBRARY" },
    { id: "models", label: "模型连接", icon: Boxes, caption: "CONNECTIONS" },
    { id: "prompts", label: "人设与提示词", icon: NotebookPen, caption: "DIRECTION" },
  ]},
  { label: "直播与记录", pages: [
    { id: "history", label: "记录与用量", icon: History, caption: "SESSION LOG" },
    { id: "subtitles", label: "直播字幕", icon: Captions, caption: "CAPTIONS" },
  ]},
  { label: "偏好设置", pages: [
    { id: "devices", label: "设备与快捷键", icon: SlidersHorizontal, caption: "AUDIO & KEYS" },
    { id: "appearance", label: "外观主题", icon: Palette, caption: "APPEARANCE" },
  ]},
];
const pages = groups.flatMap((g) => g.pages);
const shortcuts = pages.filter((p) => ["studio", "characters", "models"].includes(p.id));
type NavProps = { page: string; go: (page: string) => void };

export function PageList({ page, go, menu = false }: NavProps & { menu?: boolean }) {
  return (
    <nav className={menu ? "page-list all-pages" : "page-list side-pages"}
      aria-label={menu ? "页面导航" : "主导航"}>
      {groups.map((group) => (
        <div key={group.label}>
          <small>{group.label}</small>
          <div>{group.pages.map((p) => (
            <button key={p.id} aria-current={p.id === page ? "page" : undefined}
              onClick={() => go(p.id)}>
              <p.icon size={16} aria-hidden="true" />{p.label}
            </button>
          ))}</div>
        </div>
      ))}
    </nav>
  );
}

export function StudioMast({
  page, go, menuOpen, setMenuOpen, newSession, online, children,
}: NavProps & {
  menuOpen: boolean;
  setMenuOpen: (open: boolean) => void;
  newSession: () => void;
  online: boolean;
  children: ReactNode;
}) {
  return (
    <>
      <div className="mast-line">
        <button className="loom-brand" aria-label="声织工作台" onClick={() => go("studio")}>
          <img src={mark} width="34" height="34" alt="" />
          <span><b>声织</b><small>VOICE LOOM</small></span>
        </button>
        <div className="mast-tools">
          <span className={"local-indicator" + (online ? "" : " disconnected")}>
            <i />{online ? "本机已连接" : "服务已断开"}
          </span>
          <button className="session-new" onClick={newSession} title="新建会话" aria-label="新建会话">
            <Plus size={15} /><span>新建会话</span>
          </button>
          {children}
        </div>
      </div>
      <nav className="compact-nav" aria-label="快捷导航">
        {shortcuts.map((p) => (
          <button key={p.id} aria-current={page === p.id ? "page" : undefined} onClick={() => go(p.id)}>
            <p.icon size={15} aria-hidden="true" />{p.id === "characters" ? "角色卡" : p.label}
          </button>
        ))}
        <button aria-haspopup="dialog" aria-expanded={menuOpen}
          className={shortcuts.some((p) => page === p.id) ? "" : "more-selected"}
          onClick={() => setMenuOpen(true)}>
          <Menu size={15} aria-hidden="true" />全部页面
        </button>
      </nav>
      {menuOpen && (
        <Modal title="全部页面" onClose={() => setMenuOpen(false)}>
          <PageList page={page} go={go} menu />
        </Modal>
      )}
    </>
  );
}

export function PageHeading({ page, children }: { page: string; children: ReactNode }) {
  const item = pages.find((p) => p.id === page) || pages[0];
  return (
    <div className="page-title-block page-heading">
      <div><span className="section-code"><i />{item.caption}</span><h1>{item.label}</h1></div>
      <div className="page-actions">{children}<span className="edition">v{uiVersion}</span></div>
    </div>
  );
}
