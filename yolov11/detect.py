#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
YOLO 目标检测推理程序
支持图像、视频、文件夹、摄像头、RTSP/RTMP流等多种输入源

用法示例:
    # 检测单张图片
    python detect.py --source image.jpg --weights best.pt
    
    # 检测视频
    python detect.py --source video.mp4 --weights best.pt --save
    
    # 检测文件夹中的所有图片
    python detect.py --source ./images/ --weights best.pt
    
    # 使用摄像头(设备0)
    python detect.py --source 0 --weights best.pt --show
    
    # RTSP视频流
    python detect.py --source 'rtsp://192.168.1.100/stream' --weights best.pt
    
    # 设置置信度阈值并保存结果
    python detect.py --source image.jpg --weights best.pt --conf 0.5 --save
    
    # 只检测特定类别(如只检测人和车，类别0和2)
    python detect.py --source image.jpg --weights best.pt --classes 0 2
    
    # 使用CPU推理并显示结果
    python detect.py --source image.jpg --weights best.pt --device cpu --show
"""

import argparse
import sys
import os
from pathlib import Path
from typing import List, Union, Optional
import warnings

# 过滤掉不必要的警告
warnings.filterwarnings('ignore')

import cv2
import numpy as np
import torch
from PIL import Image

# 添加ultralytics到路径
sys.path.insert(0, str(Path(__file__).parent))

from ultralytics import YOLO
from ultralytics.engine.results import Results
from ultralytics.utils import LOGGER, colorstr, ops
from ultralytics.utils.checks import check_imshow, check_requirements
from ultralytics.utils.files import increment_path


def parse_arguments() -> argparse.Namespace:
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='YOLO 目标检测推理程序',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
输入源支持:
  - 图像文件: image.jpg, image.png
  - 视频文件: video.mp4, video.avi
  - 文件夹: ./images/, ./videos/
  - 摄像头: 0, 1, 2 (设备索引)
  - 网络流: rtsp://..., rtmp://..., http://...
  - YouTube: https://youtu.be/...
  - 通配符: 'path/*.jpg'
        """
    )
    
    # 模型参数
    parser.add_argument(
        '--weights', '-w', 
        type=str, 
        default='best.pt',
        help='模型权重文件路径 (默认: best.pt)'
    )
    parser.add_argument(
        '--source', '-s', 
        type=str, 
        default='/Users/mekeypan/opencode/projects/smart_trans/yolov11/test.jpg',
        help='输入源: 图片/视频路径、文件夹、摄像头索引、URL等'
    )
    
    # 推理参数
    parser.add_argument(
        '--conf', '-c',
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
        '--imgsz', '--img', '--img-size',
        type=int, 
        default=640,
        help='推理图像尺寸 (默认: 640)'
    )
    parser.add_argument(
        '--device',
        type=str, 
        default='',
        help='推理设备: cuda, cuda:0, cpu (默认: 自动选择)'
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
        help='每张图像最大检测数量 (默认: 300)'
    )
    parser.add_argument(
        '--classes',
        nargs='+', 
        type=int,
        help='指定要检测的类别ID，例如: --classes 0 2 4 (只检测人、车和交通灯)'
    )
    parser.add_argument(
        '--agnostic-nms',
        action='store_true',
        help='使用类别无关的NMS'
    )
    
    # 输出参数
    parser.add_argument(
        '--save',
        action='store_true',
        help='保存检测结果的图像/视频'
    )
    parser.add_argument(
        '--save-txt',
        action='store_true',
        help='将检测结果保存为txt文件'
    )
    parser.add_argument(
        '--save-crop',
        action='store_true',
        help='保存检测到的目标裁剪图'
    )
    parser.add_argument(
        '--save-dir',
        type=str, 
        default='runs/detect',
        help='结果保存目录 (默认: runs/detect)'
    )
    parser.add_argument(
        '--name',
        type=str, 
        default='exp',
        help='实验名称，用于创建保存子目录 (默认: exp)'
    )
    parser.add_argument(
        '--exist-ok',
        action='store_true',
        help='允许覆盖已存在的输出目录'
    )
    
    # 可视化参数
    parser.add_argument(
        '--show', 
        action='store_true',
        help='实时显示检测结果'
    )
    parser.add_argument(
        '--show-labels',
        action='store_true',
        default=True,
        help='显示标签'
    )
    parser.add_argument(
        '--show-conf',
        action='store_true',
        default=True,
        help='显示置信度'
    )
    parser.add_argument(
        '--show-boxes',
        action='store_true',
        default=True,
        help='显示检测框'
    )
    parser.add_argument(
        '--line-thickness',
        type=int, 
        default=2,
        help='检测框线宽 (默认: 2)'
    )
    parser.add_argument(
        '--retina-masks',
        action='store_true',
        help='使用高分辨率分割掩码'
    )
    
    # 高级参数
    parser.add_argument(
        '--stream',
        action='store_true',
        help='启用流式处理模式(适合长视频/摄像头，节省内存)'
    )
    parser.add_argument(
        '--vid-stride',
        type=int, 
        default=1,
        help='视频帧步长 (默认: 1，处理每一帧)'
    )
    parser.add_argument(
        '--batch-size',
        type=int, 
        default=1,
        help='推理批次大小 (默认: 1)'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        default=True,
        help='显示详细输出'
    )
    parser.add_argument(
        '--quiet',
        action='store_true',
        help='静默模式，减少输出'
    )
    
    return parser.parse_args()


def setup_save_dir(args: argparse.Namespace) -> Path:
    """设置保存目录"""
    save_dir = Path(args.save_dir) / args.name
    if not args.exist_ok:
        save_dir = increment_path(save_dir, exist_ok=False)
    save_dir.mkdir(parents=True, exist_ok=True)
    return save_dir


def draw_results_on_image(
    result: Results,
    line_thickness: int = 2,
    show_labels: bool = True,
    show_conf: bool = True
) -> np.ndarray:
    """
    在图像上绘制检测结果
    
    Args:
        result: YOLO检测结果
        line_thickness: 线条粗细
        show_labels: 是否显示标签
        show_conf: 是否显示置信度
        
    Returns:
        绘制了检测框的图像
    """
    # 使用YOLO内置的plot方法
    annotated_frame = result.plot(
        line_width=line_thickness,
        boxes=True,
        labels=show_labels,
        conf=show_conf,
        masks=True if result.masks is not None else False
    )
    return annotated_frame


def save_result_text(result: Results, txt_path: Path, save_conf: bool = True):
    """
    将检测结果保存为文本文件
    
    格式: <class_id> <confidence> <x_center> <y_center> <width> <height>
    """
    if result.boxes is None or len(result.boxes) == 0:
        return
    
    with open(txt_path, 'a') as f:
        for box in result.boxes:
            cls = int(box.cls)
            conf = float(box.conf)
            # 获取归一化的xywh坐标
            xywhn = box.xywhn[0].tolist()
            
            if save_conf:
                f.write(f"{cls} {conf:.6f} {' '.join(f'{x:.6f}' for x in xywhn)}\n")
            else:
                f.write(f"{cls} {' '.join(f'{x:.6f}' for x in xywhn)}\n")


def print_detection_summary(results: List[Results], source: str):
    """打印检测结果摘要"""
    LOGGER.info(f"\n{'='*60}")
    LOGGER.info(f"检测完成: {source}")
    LOGGER.info(f"{'='*60}")
    
    total_detections = 0
    for i, result in enumerate(results):
        num_dets = len(result)
        total_detections += num_dets
        
        if num_dets > 0:
            LOGGER.info(f"\n图像 {i+1}: {result.path}")
            LOGGER.info(f"检测到 {num_dets} 个目标:")
            
            # 统计各类别数量
            if result.boxes is not None:
                class_counts = {}
                for box in result.boxes:
                    cls_id = int(box.cls)
                    cls_name = result.names.get(cls_id, f"class_{cls_id}")
                    conf = float(box.conf)
                    
                    if cls_name not in class_counts:
                        class_counts[cls_name] = {'count': 0, 'max_conf': 0, 'min_conf': 1}
                    class_counts[cls_name]['count'] += 1
                    class_counts[cls_name]['max_conf'] = max(class_counts[cls_name]['max_conf'], conf)
                    class_counts[cls_name]['min_conf'] = min(class_counts[cls_name]['min_conf'], conf)
                
                for cls_name, stats in class_counts.items():
                    LOGGER.info(f"  - {cls_name}: {stats['count']}个 "
                              f"(置信度: {stats['min_conf']:.2f}-{stats['max_conf']:.2f})")
        else:
            LOGGER.info(f"\n图像 {i+1}: {result.path} - 未检测到目标")
    
    LOGGER.info(f"\n{'='*60}")
    LOGGER.info(f"总计检测到 {total_detections} 个目标")
    LOGGER.info(f"{'='*60}\n")


def run_detection(args: argparse.Namespace):
    """
    运行YOLO检测
    
    Args:
        args: 命令行参数
    """
    # 检查并设置设备
    if args.device == '':
        device = 'cuda' if torch.cuda.is_available() else 'cpu'
    else:
        device = args.device
    
    if not args.quiet:
        LOGGER.info(f"{colorstr('blue', 'bold', 'YOLO检测程序')}")
        LOGGER.info(f"设备: {device}")
        LOGGER.info(f"模型: {args.weights}")
        LOGGER.info(f"输入源: {args.source}")
        LOGGER.info(f"置信度阈值: {args.conf}")
        LOGGER.info(f"IoU阈值: {args.iou}")
    
    # 加载模型
    try:
        LOGGER.info(f"\n正在加载模型...")
        model = YOLO(args.weights)
        if not args.quiet:
            LOGGER.info(f"模型加载完成: {args.weights}")
            LOGGER.info(f"任务类型: {model.task}")
            LOGGER.info(f"模型类别: {list(model.names.values()) if hasattr(model, 'names') else 'N/A'}")
    except Exception as e:
        LOGGER.error(f"模型加载失败: {e}")
        sys.exit(1)
    
    # 设置保存目录
    save_dir = None
    if args.save or args.save_txt or args.save_crop:
        save_dir = setup_save_dir(args)
        LOGGER.info(f"结果将保存到: {save_dir}")
        
        # 创建子目录
        if args.save_crop:
            (save_dir / 'crops').mkdir(exist_ok=True)
        if args.save_txt:
            (save_dir / 'labels').mkdir(exist_ok=True)
    
    # 检查是否可以显示图像
    if args.show:
        try:
            check_imshow(warn=True)
        except Exception as e:
            LOGGER.warning(f"无法显示图像: {e}")
            args.show = False
    
    # 准备预测参数
    predict_kwargs = {
        'conf': args.conf,
        'iou': args.iou,
        'imgsz': args.imgsz,
        'device': device,
        'half': args.half,
        'max_det': args.max_det,
        'classes': args.classes,
        'agnostic_nms': args.agnostic_nms,
        'vid_stride': args.vid_stride,
        'stream': args.stream,
        'verbose': args.verbose and not args.quiet,
        'save': args.save,
        'save_txt': args.save_txt,
        'save_crop': args.save_crop,
        'show': args.show,
        'project': args.save_dir if args.save else None,
        'name': args.name if args.save else None,
        'exist_ok': args.exist_ok,
        'line_width': args.line_thickness,
        'show_labels': args.show_labels,
        'show_conf': args.show_conf,
        'show_boxes': args.show_boxes,
        'retina_masks': args.retina_masks,
    }
    
    # 移除None值
    predict_kwargs = {k: v for k, v in predict_kwargs.items() if v is not None}
    
    # 执行检测
    try:
        LOGGER.info(f"\n开始检测...")
        
        # 运行预测
        results = model.predict(args.source, **predict_kwargs)
        
        # 处理结果
        if args.stream:
            # 流式模式: 逐个处理结果
            processed_results = []
            for i, result in enumerate(results):
                processed_results.append(result)
                
                # 实时打印
                if not args.quiet and args.verbose:
                    LOGGER.info(f"处理帧 {i+1}: 检测到 {len(result)} 个目标")
                
                # 手动保存(如果需要)
                if save_dir and not args.save:
                    # 保存标注后的图像
                    annotated = draw_results_on_image(
                        result, 
                        args.line_thickness,
                        args.show_labels,
                        args.show_conf
                    )
                    save_path = save_dir / f"result_{i:06d}.jpg"
                    cv2.imwrite(str(save_path), annotated)
                    
                    # 保存txt
                    if args.save_txt:
                        txt_path = save_dir / 'labels' / f"result_{i:06d}.txt"
                        save_result_text(result, txt_path, args.show_conf)
            
            results = processed_results
        else:
            # 非流式模式: 结果已经是列表
            if not args.quiet:
                LOGGER.info(f"处理完成，共 {len(results)} 帧/图像")
        
        # 打印检测摘要
        if not args.quiet:
            print_detection_summary(results, args.source)
        
        # 返回结果供进一步处理
        return results
        
    except KeyboardInterrupt:
        LOGGER.info("\n检测被用户中断")
        sys.exit(0)
    except Exception as e:
        LOGGER.error(f"检测过程出错: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def demo_detection():
    """简单的演示检测 - 用于快速测试"""
    LOGGER.info(f"{colorstr('yellow', 'bold', '运行演示模式')}")
    
    # 加载模型
    model_path = "best.pt"
    image_path = "/Users/mekeypan/opencode/projects/smart_trans/yolov11/test.jpg"
    
    if not os.path.exists(model_path):
        LOGGER.error(f"模型文件不存在: {model_path}")
        LOGGER.info("请确保best.pt在当前目录下，或修改model_path变量")
        return
    
    if not os.path.exists(image_path):
        LOGGER.error(f"测试图像不存在: {image_path}")
        return
    
    LOGGER.info(f"加载模型: {model_path}")
    model = YOLO(model_path)
    
    LOGGER.info(f"检测图像: {image_path}")
    results = model(image_path, verbose=True)
    
    # 显示结果
    for result in results:
        LOGGER.info(f"\n检测到 {len(result)} 个目标:")
        
        if result.boxes is not None:
            for box in result.boxes:
                cls_id = int(box.cls)
                cls_name = result.names.get(cls_id, f"class_{cls_id}")
                conf = float(box.conf)
                xyxy = box.xyxy[0].tolist()
                LOGGER.info(f"  - {cls_name}: 置信度={conf:.2f}, 位置=({xyxy[0]:.1f}, {xyxy[1]:.1f}, {xyxy[2]:.1f}, {xyxy[3]:.1f})")
        
        # 显示图像
        result.show()
    
    LOGGER.info("\n演示完成!")


def main():
    """主函数"""
    # 如果没有命令行参数(只有脚本名)，运行演示模式
    if len(sys.argv) == 1:
        demo_detection()
        return
    
    # 解析参数
    args = parse_arguments()
    
    # 运行检测
    results = run_detection(args)
    
    # 可以在这里添加对results的进一步处理
    # 例如: 数据分析、导出特定格式等
    
    LOGGER.info(f"{colorstr('green', 'bold', '检测程序执行完毕')}")


if __name__ == "__main__":
    main()
