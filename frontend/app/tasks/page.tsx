"use client";

import { useRef, useState } from "react";
import {
  Button,
  Card,
  Col,
  Progress,
  Row,
  Select,
  Upload,
  message,
} from "antd";
import { InboxOutlined, RocketOutlined } from "@ant-design/icons";
import AppShell from "@/components/AppShell";
import { api } from "@/lib/api";
import { INDUSTRY_OPTIONS, POI_CITIES } from "@/lib/constants";

const { Dragger } = Upload;

export default function TasksPage() {
  const [city, setCity] = useState<string>("苏州");
  const [industries, setIndustries] = useState<string[]>(["semiconductor"]);
  const [starting, setStarting] = useState(false);
  const [progress, setProgress] = useState<number | null>(null);
  const [logs, setLogs] = useState<string[]>([]);
  const esRef = useRef<EventSource | null>(null);

  const subscribeTask = (taskId: string) => {
    esRef.current?.close();
    const base = process.env.NEXT_PUBLIC_API_BASE || "http://127.0.0.1:8000";
    const es = new EventSource(`${base}/api/tasks/${taskId}/events`);
    esRef.current = es;
    es.onmessage = (ev) => {
      try {
        const payload = JSON.parse(ev.data);
        if (payload.type === "progress") {
          setProgress(payload.progress ?? 0);
          if (payload.log) setLogs((prev) => [...prev.slice(-200), String(payload.log)]);
        } else if (payload.type === "done") {
          setProgress(100);
          setLogs((prev) => [...prev, "✅ 任务完成"]);
          es.close();
          setStarting(false);
        } else if (payload.type === "error") {
          setLogs((prev) => [...prev, `❌ ${payload.message}`]);
          es.close();
          setStarting(false);
        }
      } catch {
        /* 忽略非 JSON 帧 */
      }
    };
    es.onerror = () => {
      es.close();
      setStarting(false);
    };
  };

  const startPoi = async () => {
    if (industries.length === 0) {
      message.warning("请至少选择一个下游行业");
      return;
    }
    setStarting(true);
    setProgress(0);
    setLogs(["任务提交中…"]);
    try {
      const res = await api.post<{ task_id: string }>("/api/tasks/poi", {
        city,
        industry_keys: industries,
      });
      message.success("任务已启动");
      subscribeTask(res.task_id);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "启动失败");
      setStarting(false);
    }
  };

  const uploadCsv = async (file: File, kind: "enterprises" | "tenders") => {
    const fd = new FormData();
    fd.append("file", file);
    try {
      const res = await api.post<{ inserted?: number; signals_created?: number; merged?: number }>(
        `/api/import/${kind}`,
        fd,
      );
      message.success(
        kind === "enterprises"
          ? `导入完成：新增 ${res.inserted ?? 0} 家，合并 ${res.merged ?? 0} 家`
          : `解析完成：生成信号 ${res.signals_created ?? 0} 条`,
      );
      setLogs((prev) => [...prev, `📄 ${file.name} 导入成功`]);
    } catch (e) {
      message.error(e instanceof Error ? e.message : "导入失败");
    }
    return false;
  };

  return (
    <AppShell>
      <Row gutter={16}>
        <Col span={12}>
          <Card title="🛰 高德 POI 地图拓客" extra={<RocketOutlined />}>
            <div className="flex gap-3 mb-4">
              <Select
                style={{ width: 140 }}
                value={city}
                onChange={setCity}
                options={POI_CITIES.map((c) => ({ value: c, label: c }))}
              />
              <Select
                mode="multiple"
                style={{ flex: 1 }}
                placeholder="选择下游行业"
                value={industries}
                onChange={setIndustries}
                options={INDUSTRY_OPTIONS.map((i) => ({ value: i.key, label: i.name }))}
              />
              <Button type="primary" loading={starting} onClick={startPoi}>
                开始采集
              </Button>
            </div>
            <p className="text-gray-400 text-xs">
              按行业关键词扫描所选城市的工厂 POI，自动入库、归一去重、ICP 打分。需在「设置」配置高德 Key。
            </p>
          </Card>

          <Card title="📥 CSV 数据导入" className="mt-4">
            <Dragger
              accept=".csv"
              showUploadList={false}
              beforeUpload={(f) => uploadCsv(f, "enterprises")}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖入【企业名单 CSV】</p>
              <p className="ant-upload-hint">列：name,province,city,district,address,business_scope,registered_capital_wan,industry_key</p>
            </Dragger>
            <Dragger
              accept=".csv"
              showUploadList={false}
              className="mt-3"
              beforeUpload={(f) => uploadCsv(f, "tenders")}
            >
              <p className="ant-upload-drag-icon"><InboxOutlined /></p>
              <p className="ant-upload-text">点击或拖入【招标公告 CSV】</p>
              <p className="ant-upload-hint">列：title,content,url,pub_date,enterprise_name — 命中设备词自动生成意图信号</p>
            </Dragger>
          </Card>
        </Col>

        <Col span={12}>
          <Card title="实时进度">
            {progress === null ? (
              <p className="text-gray-400">尚无运行中的任务</p>
            ) : (
              <>
                <Progress percent={progress} status={progress === 100 ? "success" : "active"} />
                <pre className="bg-black text-green-400 text-xs p-3 rounded mt-3 max-h-96 overflow-auto whitespace-pre-wrap">
                  {logs.join("\n") || "等待日志…"}
                </pre>
              </>
            )}
          </Card>
        </Col>
      </Row>
    </AppShell>
  );
}
