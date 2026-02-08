# project_spatial_website

一个基于 **FastAPI + Scanpy / Squidpy** 的轻量级 Web 服务，用于**空间转录组基因表达的可视化展示**。

本项目面向科研使用场景：  
用户通过网页提交基因名和数据集信息，服务器在后端完成绘图，并返回**预渲染的高分辨率空间表达图像**。

---

## 项目特点

- 基于 **FastAPI** 的后端服务
- 使用 **Squidpy / Scanpy** 进行空间转录组可视化
- 服务端绘图（PDF → PNG），避免前端计算负担
- 基于文件的缓存机制，减少重复绘图
- 简单的登录 / 会话管理
- 设计目标为 **单节点云服务器部署（CPU）**

---

## 项目结构

```

project_spatial_website/
├── app/
│   ├── main.py        # FastAPI 入口
│   ├── render.py     # 核心绘图逻辑
│   ├── cache.py      # 缓存与清理策略
│   ├── auth.py       # 登录与会话校验
│   └── config.py     # 全局配置
├── templates/
│   ├── base.html
│   ├── login.html
│   └── dashboard.html
├── data/              # （不纳入 git）空间转录组 AnnData 数据
├── figures/           # （不纳入 git）绘图输出结果
├── logs/              # （不纳入 git）运行日志
└── README.md

````

> 说明：  
> 数据文件（如 `.h5ad`）、绘图结果、缓存和日志文件均**不纳入版本控制**。

---

## 运行环境要求

- Python ≥ 3.9
- FastAPI
- Uvicorn
- Scanpy
- Squidpy
- Matplotlib
- NumPy / SciPy

示例环境创建方式：

```bash
conda create -n spatial_web python=3.10
conda activate spatial_web
pip install fastapi uvicorn scanpy squidpy matplotlib
````

---

## 本地运行（开发模式）

在项目根目录下执行：

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

浏览器访问：

```
http://127.0.0.1:8000
```

---

## 设计说明（简要）

* 每个请求在**单进程中完成一次绘图**
* 不依赖多线程 / GPU 并行
* 并发通过 **请求级别并发 + 缓存** 实现
* 适合：

  * 预渲染常用基因
  * 低到中等并发科研访问场景

---

## 数据假设

* 输入数据为 `.h5ad` 格式的空间转录组 AnnData
* 已包含空间坐标与必要的表达层
* 不在服务端对原始数据进行修改

---

## 许可证

MIT License
