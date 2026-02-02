#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO CPU 推理性能测试脚本
用于测试CPU模式下持续检测的平均推理时间

用法示例:
    # 测试单张图片（重复检测100次）
    python benchmark_cpu.py --source image.jpg --weights best.pt --iterations 100
    
    # 测试视频文件（统计所有帧的平均推理时间）
    python benchmark_cpu.py --source video.mp4 --weights best.pt
    
    # 测试摄像头10秒
    python benchmark_cpu.py --source 0 --weights best.pt --duration 10
    
    # 使用特定模型配置
    python benchmark_cpu.py --source image.jpg --weights best.pt --imgsz 640 --conf 0.25
    
    # 详细模式（显示每帧耗时）
    python benchmark_cpu.py --source image.jpg --weights best.pt --verbose
    
    # 保存性能报告
    python benchmark_cpu.py --source image.jpg --weights best.pt --save-report
"""

import argparse
import sys
import os
import time
import statistics
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
import json
import warnings

warnings.filterwarnings('ignore')

import cv2
import numpy as np
import torch
from PIL import Image

sys.path.insert(0, str(Path(__file__).parent))

from ultralytics import YOLO
from ultralytics.engine.results import Results
from ultralytics.utils import LOGGER, colorstr
from ultralytics.utils.checks import check_imshow


class PerformanceMetrics:
    """性能指标收集器"""
    
    def __init__(self):
        self.preprocess_times: List[float] = []
        self.inference_times: List[float] = []
        self.postprocess_times: List[float] = []
        self.total_times: List[float] = []
        self.fps_list: List[float] = []
        self.detection_counts: List[int] = []
        self.start_time: Optional[float] = None
        self.end_time: Optional[float] = None
        
    def add_record(self, result: Results, total_time: float):
        """添加一条性能记录"""
        # 从result中获取各阶段时间（ms）
        if result.speed:
            self.preprocess_times.append(result.speed.get('preprocess', 0))
            self.inference_times.append(result.speed.get('inference', 0))
            self.postprocess_times.append(result.speed.get('postprocess', 0))
        
        self.total_times.append(total_time * 1000)  # 转换为ms
        self.fps_list.append(1000.0 / (total_time * 1000) if total_time > 0 else 0)
        self.detection_counts.append(len(result))
    
    def get_summary(self) -> Dict:
        """获取性能汇总"""
        if not self.total_times:
            return {}
        
        summary = {
            '样本数量': len(self.total_times),
            '预处理时间(ms)': {
                '平均': statistics.mean(self.preprocess_times) if self.preprocess_times else 0,
                '中位数': statistics.median(self.preprocess_times) if self.preprocess_times else 0,
                '最小': min(self.preprocess_times) if self.preprocess_times else 0,
                '最大': max(self.preprocess_times) if self.preprocess_times else 0,
            },
            '推理时间(ms)': {
                '平均': statistics.mean(self.inference_times) if self.inference_times else 0,
                '中位数': statistics.median(self.inference_times) if self.inference_times else 0,
                '最小': min(self.inference_times) if self.inference_times else 0,
                '最大': max(self.inference_times) if self.inference_times else 0,
            },
            '后处理时间(ms)': {
                '平均': statistics.mean(self.postprocess_times) if self.postprocess_times else 0,
                '中位数': statistics.median(self.postprocess_times) if self.postprocess_times else 0,
                '最小': min(self.postprocess_times) if self.postprocess_times else 0,
                '最大': max(self.postprocess_times) if self.postprocess_times else 0,
            },
            '总时间(ms)': {
                '平均': statistics.mean(self.total_times),
                '中位数': statistics.median(self.total_times),
                '最小': min(self.total_times),
                '最大': max(self.total_times),
                '标准差': statistics.stdev(self.total_times) if len(self.total_times) > 1 else 0,
            },
            'FPS': {
                '平均': statistics.mean(self.fps_list),
                '中位数': statistics.median(self.fps_list),
                '最小': min(self.fps_list),
                '最大': max(self.fps_list),
            },
            '检测目标数': {
                '平均每帧': statistics.mean(self.detection_counts),
                '总计': sum(self.detection_counts),
            }
        }
        return summary
    
    def print_report(self):
        """打印性能报告"""
        summary = self.get_summary()
        if not summary:
            LOGGER.warning("没有收集到性能数据")
            return
        
        LOGGER.info(f"\n{'='*70}")
        LOGGER.info(f"{' '*20}CPU 推理性能测试报告")
        LOGGER.info(f"{'='*70}")
        LOGGER.info(f"测试样本数: {summary['样本数量']}")
        LOGGER.info(f"{'-'*70}")
        
        # 各阶段时间
        LOGGER.info(f"\n【各阶段耗时】(毫秒)")
        LOGGER.info(f"{'阶段':<15} {'平均':>10} {'中位数':>10} {'最小':>10} {'最大':>10}")
        LOGGER.info(f"{'-'*60}")
        
        stages = [
            ('预处理', summary['预处理时间(ms)']),
            ('模型推理', summary['推理时间(ms)']),
            ('后处理', summary['后处理时间(ms)']),
            ('总计', summary['总时间(ms)']),
        ]
        
        for name, times in stages:
            LOGGER.info(f"{name:<15} {times['平均']:>10.2f} {times['中位数']:>10.2f} "
                       f"{times['最小']:>10.2f} {times['最大']:>10.2f}")
        
        # FPS
        LOGGER.info(f"\n【FPS性能】")
        fps = summary['FPS']
        LOGGER.info(f"  平均FPS: {fps['平均']:.2f}")
        LOGGER.info(f"  中位数FPS: {fps['中位数']:.2f}")
        LOGGER.info(f"  FPS范围: {fps['最小']:.2f} - {fps['最大']:.2f}")
        
        # 检测统计
        LOGGER.info(f"\n【检测统计】")
        LOGGER.info(f"  平均每帧检测目标数: {summary['检测目标数']['平均每帧']:.1f}")
        LOGGER.info(f"  总检测目标数: {summary['检测目标数']['总计']}")
        
        LOGGER.info(f"{'='*70}\n")
    
    def save_report(self, filepath: Path):
        """保存报告到JSON文件"""
        summary = self.get_summary()
        report = {
            'timestamp': datetime.now().isoformat(),
            'device': 'cpu',
            'metrics': summary
        }
        
        with open(filepath, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2, ensure_ascii=False)
        
        LOGGER.info(f"性能报告已保存: {filepath}")


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='YOLO CPU 推理性能测试脚本',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
测试模式说明:
  1. 图片模式: 对单张图片重复检测N次，统计平均时间
  2. 视频模式: 逐帧检测，统计所有帧的平均时间
  3. 摄像头模式: 持续检测指定时长，统计平均时间

示例:
  python benchmark_cpu.py -s image.jpg -w best.pt -n 100
  python benchmark_cpu.py -s video.mp4 -w best.pt
  python benchmark_cpu.py -s 0 -w best.pt -t 10
        """
    )
    
    # 基本参数
    parser.add_argument(
        '--weights', '-w',
        type=str,
        default='best.pt',
        help='模型权重文件路径 (默认: best.pt)'
    )
    parser.add_argument(
        '--source', '-s',
        type=str,
        required=True,
        help='输入源: 图片路径、视频路径、摄像头索引(0/1/2)'
    )
    
    # 测试参数
    parser.add_argument(
        '--iterations', '-n',
        type=int,
        default=100,
        help='图片模式下的重复检测次数 (默认: 100)'
    )
    parser.add_argument(
        '--duration', '-t',
        type=int,
        default=None,
        help='摄像头模式的测试时长(秒)，默认持续检测直到按Ctrl+C'
    )
    parser.add_argument(
        '--warmup',
        type=int,
        default=10,
        help='预热次数，预热不计入统计 (默认: 10)'
    )
    
    # 推理参数
    parser.add_argument(
        '--imgsz',
        type=int,
        default=640,
        help='输入图像尺寸 (默认: 640)'
    )
    parser.add_argument(
        '--conf',
        type=float,
        default=0.25,
        help='置信度阈值 (默认: 0.25)'
    )
    parser.add_argument(
        '--iou',
        type=float,
        default=0.45,
        help='NMS IoU阈值 (默认: 0.45)'
    )
    parser.add_argument(
        '--half',
        action='store_true',
        help='使用FP16半精度推理'
    )
    parser.add_argument(
        '--max-det',
        type=int,
        default=300,
        help='最大检测数量 (默认: 300)'
    )
    
    # 输出参数
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='显示每帧的详细耗时'
    )
    parser.add_argument(
        '--save-report',
        action='store_true',
        help='保存性能报告到JSON文件'
    )
    parser.add_argument(
        '--save-dir',
        type=str,
        default='benchmark_results',
        help='报告保存目录 (默认: benchmark_results)'
    )
    
    return parser.parse_args()


def detect_source_type(source: str) -> str:
    """检测输入源类型"""
    if source.isdigit():
        return 'camera'
    
    source_path = Path(source)
    if source_path.is_file():
        suffix = source_path.suffix.lower()
        if suffix in ['.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp']:
            return 'image'
        elif suffix in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv']:
            return 'video'
    
    return 'unknown'


def benchmark_image(model, image_path: str, args: argparse.Namespace) -> PerformanceMetrics:
    """对单张图片进行多次推理测试"""
    metrics = PerformanceMetrics()
    
    LOGGER.info(f"\n{'='*70}")
    LOGGER.info(f"图片模式: {image_path}")
    LOGGER.info(f"重复检测次数: {args.iterations} (预热: {args.warmup})")
    LOGGER.info(f"{'='*70}\n")
    
    # 预热阶段
    LOGGER.info("预热中...")
    for i in range(args.warmup):
        start = time.time()
        results = model(image_path, verbose=False, device='cpu')
        _ = time.time() - start
    LOGGER.info(f"预热完成 ({args.warmup}次)\n")
    
    # 正式测试
    LOGGER.info("开始测试...")
    for i in range(args.iterations):
        start = time.time()
        results = model(image_path, verbose=False, device='cpu')
        total_time = time.time() - start
        
        result = results[0]
        metrics.add_record(result, total_time)
        
        if args.verbose:
            LOGGER.info(f"  第{i+1:3d}/{args.iterations}次: "
                       f"预处理={result.speed.get('preprocess', 0):.2f}ms, "
                       f"推理={result.speed.get('inference', 0):.2f}ms, "
                       f"后处理={result.speed.get('postprocess', 0):.2f}ms, "
                       f"总计={total_time*1000:.2f}ms, "
                       f"目标数={len(result)}")
        else:
            if (i + 1) % 10 == 0 or i == args.iterations - 1:
                LOGGER.info(f"  进度: {i+1}/{args.iterations}")
    
    return metrics


def benchmark_video(model, video_path: str, args: argparse.Namespace) -> PerformanceMetrics:
    """对视频文件进行推理测试"""
    metrics = PerformanceMetrics()
    
    LOGGER.info(f"\n{'='*70}")
    LOGGER.info(f"视频模式: {video_path}")
    LOGGER.info(f"{'='*70}\n")
    
    # 打开视频
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        LOGGER.error(f"无法打开视频: {video_path}")
        return metrics
    
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = cap.get(cv2.CAP_PROP_FPS)
    LOGGER.info(f"视频信息: {total_frames}帧, {fps:.2f}FPS, 时长{total_frames/fps:.1f}秒")
    
    # 预热
    LOGGER.info("\n预热中...")
    ret, frame = cap.read()
    if ret:
        for i in range(args.warmup):
            results = model(frame, verbose=False, device='cpu')
    cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
    LOGGER.info(f"预热完成\n")
    
    # 正式测试
    LOGGER.info("开始测试...")
    frame_count = 0
    
    while True:
        ret, frame = cap.read()
        if not ret:
            break
        
        start = time.time()
        results = model(frame, verbose=False, device='cpu')
        total_time = time.time() - start
        
        result = results[0]
        metrics.add_record(result, total_time)
        frame_count += 1
        
        if args.verbose:
            LOGGER.info(f"  帧{frame_count:4d}: "
                       f"推理={result.speed.get('inference', 0):.2f}ms, "
                       f"总计={total_time*1000:.2f}ms")
        else:
            if frame_count % 30 == 0 or frame_count == total_frames:
                LOGGER.info(f"  进度: {frame_count}/{total_frames}帧 "
                           f"({frame_count/total_frames*100:.1f}%)")
    
    cap.release()
    LOGGER.info(f"\n完成: 共处理{frame_count}帧")
    
    return metrics


def benchmark_camera(model, camera_id: int, args: argparse.Namespace) -> PerformanceMetrics:
    """对摄像头进行推理测试"""
    metrics = PerformanceMetrics()
    
    LOGGER.info(f"\n{'='*70}")
    LOGGER.info(f"摄像头模式: 设备{camera_id}")
    if args.duration:
        LOGGER.info(f"测试时长: {args.duration}秒")
    else:
        LOGGER.info("持续检测中... (按Ctrl+C停止)")
    LOGGER.info(f"{'='*70}\n")
    
    # 打开摄像头
    cap = cv2.VideoCapture(camera_id)
    if not cap.isOpened():
        LOGGER.error(f"无法打开摄像头: {camera_id}")
        return metrics
    
    # 设置分辨率
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
    
    # 预热
    LOGGER.info("预热中...")
    for i in range(args.warmup):
        ret, frame = cap.read()
        if ret:
            results = model(frame, verbose=False, device='cpu')
    LOGGER.info(f"预热完成 ({args.warmup}次)\n")
    
    # 正式测试
    LOGGER.info("开始测试...")
    frame_count = 0
    start_time = time.time()
    
    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                LOGGER.warning("无法读取摄像头帧")
                break
            
            inference_start = time.time()
            results = model(frame, verbose=False, device='cpu')
            total_time = time.time() - inference_start
            
            result = results[0]
            metrics.add_record(result, total_time)
            frame_count += 1
            
            if args.verbose:
                current_fps = 1000.0 / (total_time * 1000) if total_time > 0 else 0
                LOGGER.info(f"  帧{frame_count:4d}: "
                           f"推理={result.speed.get('inference', 0):.2f}ms, "
                           f"FPS={current_fps:.2f}")
            else:
                if frame_count % 30 == 0:
                    avg_time = statistics.mean(metrics.total_times[-30:])
                    avg_fps = 1000.0 / avg_time if avg_time > 0 else 0
                    LOGGER.info(f"  已处理{frame_count}帧, 最近30帧平均FPS: {avg_fps:.2f}")
            
            # 检查是否达到指定时长
            if args.duration and (time.time() - start_time) >= args.duration:
                LOGGER.info(f"\n已达到指定时长: {args.duration}秒")
                break
                
    except KeyboardInterrupt:
        LOGGER.info("\n\n用户中断测试")
    
    cap.release()
    LOGGER.info(f"\n完成: 共处理{frame_count}帧, 实际测试时长: {time.time()-start_time:.1f}秒")
    
    return metrics


def main():
    """主函数"""
    args = parse_arguments()
    
    # 强制使用CPU
    device = 'cpu'
    
    # 打印测试信息
    LOGGER.info(f"\n{colorstr('blue', 'bold', 'YOLO CPU 推理性能测试')}")
    LOGGER.info(f"设备: {device}")
    LOGGER.info(f"模型: {args.weights}")
    LOGGER.info(f"输入尺寸: {args.imgsz}")
    LOGGER.info(f"置信度阈值: {args.conf}")
    LOGGER.info(f"IoU阈值: {args.iou}")
    
    # 加载模型
    try:
        LOGGER.info(f"\n加载模型...")
        model = YOLO(args.weights)
        LOGGER.info(f"模型加载完成")
        LOGGER.info(f"任务类型: {model.task}")
        LOGGER.info(f"类别数: {len(model.names) if hasattr(model, 'names') else 'N/A'}")
    except Exception as e:
        LOGGER.error(f"模型加载失败: {e}")
        sys.exit(1)
    
    # 检测输入源类型
    source_type = detect_source_type(args.source)
    LOGGER.info(f"输入源类型: {source_type}")
    
    # 根据类型执行测试
    if source_type == 'image':
        metrics = benchmark_image(model, args.source, args)
    elif source_type == 'video':
        metrics = benchmark_video(model, args.source, args)
    elif source_type == 'camera':
        metrics = benchmark_camera(model, int(args.source), args)
    else:
        LOGGER.error(f"不支持的输入源类型: {args.source}")
        LOGGER.error("支持的类型: 图片文件(.jpg/.png)、视频文件(.mp4/.avi)、摄像头索引(0/1/2)")
        sys.exit(1)
    
    # 打印性能报告
    metrics.print_report()
    
    # 保存报告
    if args.save_report:
        save_dir = Path(args.save_dir)
        save_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = save_dir / f"benchmark_{source_type}_{timestamp}.json"
        metrics.save_report(report_file)
    
    LOGGER.info(f"{colorstr('green', 'bold', '测试完成')}")


if __name__ == "__main__":
    main()
