import { useEffect, useRef, type ReactNode } from "react";
import {
  AudioLines,
  Boxes,
  Captions,
  Contact,
  History,
  Menu,
  NotebookPen,
  Palette,
  Plus,
  Radio,
  Settings2,
  SlidersHorizontal,
  Users,
  Layers3,
  Disc3,
} from "lucide-react";
import { Modal } from "./components";
import { version as uiVersion } from "../package.json";
const mark = new URL("./brand.svg", import.meta.url).href;

const sections = [
  {
    label: "工作台",
    icon: AudioLines,
    pages: [{ id: "studio", label: "工作台", icon: AudioLines }],
  },
  {
    label: "声音库",
    icon: Disc3,
    pages: [
      { id: "voices", label: "参考音色", icon: Users },
      { id: "characters", label: "角色卡与读音", icon: Contact },
      { id: "prompts", label: "人设与提示词", icon: NotebookPen },
    ],
  },
  {
    label: "直播与记录",
    icon: Radio,
    pages: [
      { id: "history", label: "记录与用量", icon: History },
      { id: "subtitles", label: "直播字幕", icon: Captions },
    ],
  },
  {
    label: "设置",
    icon: Settings2,
    pages: [
      { id: "models", label: "模型连接", icon: Boxes },
      { id: "devices", label: "设备与快捷键", icon: SlidersHorizontal },
      { id: "appearance", label: "外观主题", icon: Palette },
    ],
  },
];
export function sectionFor(page: string) {
  return sections.find((section) => section.pages.some((p) => p.id === page)) || sections[0];
}
export function StudioMast({
  page,
  go,
  menuOpen,
  setMenuOpen,
  newSession,
  online,
  children,
}: {
  page: string;
  go: (page: string) => void;
  menuOpen: boolean;
  setMenuOpen: (open: boolean) => void;
  newSession: () => void;
  online: boolean;
  children: ReactNode;
}) {
  const current = sectionFor(page);
  const lastPage = useRef<Record<string, string>>({});
  useEffect(() => {
    lastPage.current[current.label] = page;
  }, [current, page]);
  return (
    <>
      <div className="mast-line">
        <button
          className="mast-menu icon"
          aria-label="打开导航"
          aria-haspopup="dialog"
          onClick={() => setMenuOpen(true)}
        >
          <Menu size={19} />
        </button>
        <button className="loom-brand" aria-label="声织工作台" onClick={() => go("studio")}>
          <img src={mark} width="34" height="34" alt="" />
          <span>
            <b>声织</b>
            <small>VOICE LOOM</small>
          </span>
        </button>
        <nav className="area-nav" aria-label="工作区导航">
          {sections.map((group) => (
            <button
              key={group.label}
              aria-current={current === group ? "true" : undefined}
              onClick={() => go(lastPage.current[group.label] || group.pages[0].id)}
            >
              <group.icon size={16} />
              {group.label}
            </button>
          ))}
        </nav>
        <div className="mast-tools">
          <span className={"local-indicator" + (online ? "" : " disconnected")}>
            <i />
            {online ? "本机已连接" : "服务已断开"}
          </span>
          <button className="session-new" onClick={newSession} title="新建会话">
            <Plus size={15} />
            <span>新建会话</span>
          </button>
          {children}
        </div>
      </div>
      {menuOpen && (
        <Modal title="页面导航" onClose={() => setMenuOpen(false)}>
          <nav className="all-pages" aria-label="页面导航">
            {sections.map((group) => (
              <div key={group.label}>
                <small>{group.label}</small>
                <div>
                  {group.pages.map((p) => (
                    <button
                      key={p.id}
                      aria-current={p.id === page ? "page" : undefined}
                      onClick={() => go(p.id)}
                    >
                      <p.icon size={16} />
                      {p.label}
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </nav>
          <button
            className="full"
            onClick={() => {
              setMenuOpen(false);
              newSession();
            }}
          >
            <Plus size={16} />
            新建会话
          </button>
        </Modal>
      )}
    </>
  );
}

export function PageHeading({
  page,
  go,
  children,
}: {
  page: string;
  go: (page: string) => void;
  children: ReactNode;
}) {
  const current = sectionFor(page);
  const item = current.pages.find((p) => p.id === page)!;
  const captions: Record<string, string> = {
    studio: "RECORDING DESK",
    voices: "VOICE LIBRARY",
    characters: "CHARACTERS",
    prompts: "DIRECTION",
    models: "CONNECTIONS",
    devices: "AUDIO & KEYS",
    appearance: "APPEARANCE",
    history: "SESSION LOG",
    subtitles: "CAPTIONS",
  };
  return (
    <div className="page-title-block">
      <div className="page-heading">
        <div>
          <span className="section-code">
            <i />
            {captions[page]}
          </span>
          <h1>{item.label}</h1>
        </div>
        <div className="page-actions">
          {children}
          <span className="edition">v{uiVersion}</span>
        </div>
      </div>
      {current.pages.length > 1 && (
        <nav className="sub-nav" aria-label={current.label + "分区"}>
          <Layers3 size={14} aria-hidden="true" />
          {current.pages.map((p) => (
            <button
              key={p.id}
              aria-current={p.id === page ? "page" : undefined}
              onClick={() => go(p.id)}
            >
              {p.label}
            </button>
          ))}
        </nav>
      )}
    </div>
  );
}
