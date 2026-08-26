#!/usr/bin/env python3
"""一键端口对调：cnb2api 挪 :17863，ToolForge 顶 :7863（公网面）。

用法:
  python3 scripts/setup_toolforge.py --client-key <给客户端的key> [--cnb-key <cnb2api的key>]

不改则用占位符，事后手动编辑 docker/config-local.yaml。
"""
import argparse, json, pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--client-key", default="<client-key>", help="客户端访问 ToolForge 的 key")
ap.add_argument("--cnb-key", default="<cnb2api-key>", help="cnb2api config.json 里的 api_key")
args = ap.parse_args()

# 1) cnb2api -> :17863
cfg = ROOT / "config.json"
if cfg.exists():
    c = json.loads(cfg.read_text())
    c["listen"] = ":17863"
    cfg.write_text(json.dumps(c, indent=2))
    print(f"OK {cfg.name}: listen -> :17863")
else:
    print(f"跳过 {cfg}（不存在，先 cp config.example.json config.json）")

# 2) ToolForge -> :7863，上游指 17863
tf = ROOT / "docker" / "config-local.yaml"
ex = ROOT / "docker" / "config-local.example.yaml"
src = tf if tf.exists() else ex
s = src.read_text()
s = s.replace("port: 18080", "port: 7863").replace("port: 18081", "port: 7863")
s = s.replace("http://localhost:17863/v1", "@@UP@@")  # 防重复替换
s = s.replace("http://localhost:7863/v1", "http://localhost:17863/v1")
s = s.replace("@@UP@@", "http://localhost:17863/v1")
s = s.replace("<给客户端用的key，自定义>", args.client_key)
s = s.replace("<cnb2api 的 api_key，与 config.json 一致>", args.cnb_key)
tf.write_text(s)
print(f"OK {tf.relative_to(ROOT)}: port->7863 upstream->17863 keys 已写入")
print("\n完成。启动顺序见 README「快速开始」。")
