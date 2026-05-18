import numpy as np
import argparse


def generate_step_commands(duration=120, low_bw=2, high_bw=8, period=40):
    """阶跃波：低→高→低"""
    commands = []
    t = 0
    while t < duration:
        phase = (t % period) / period
        bw = high_bw if phase < 0.5 else low_bw
        commands.append((t, round(bw, 2)))
        t += 3
    return commands


def generate_sine_commands(duration=120, center=4.5, amplitude=3.5, period=30):
    """正弦波：平滑振荡"""
    commands = []
    t = 0
    while t < duration:
        bw = center + amplitude * np.sin(2 * np.pi * t / period)
        bw = max(0.5, min(9.5, bw))
        commands.append((t, round(bw, 2)))
        t += 3
    return commands


def generate_sawtooth_noise_commands(
    duration=120, period=30, base_min=2, base_max=8, noise_sigma=0.5
):
    commands = []
    t = 0
    while t < duration:
        phase = (t % period) / period
        base_bw = base_min + (base_max - base_min) * phase
        noisy_bw = base_bw + np.random.normal(0, noise_sigma)
        noisy_bw = max(0.5, min(9.5, noisy_bw))
        commands.append((t, round(noisy_bw, 2)))
        t += 3
    return commands


def generate_fat_tree_commands(duration=120, center=2.0, amplitude=1.5, period=30):
    """Lower-bandwidth traffic for Fat-Tree (more concurrent flows)."""
    commands = []
    t = 0
    while t < duration:
        bw = center + amplitude * np.sin(2 * np.pi * t / period)
        bw = max(0.3, min(4.0, bw))
        commands.append((t, round(bw, 2)))
        t += 3
    return commands


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--pattern", choices=["step", "sine", "sawtooth"], default="sawtooth"
    )
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--noise-sigma", type=float, default=0.5)
    args = parser.parse_args()

    if args.pattern == "step":
        cmds = generate_step_commands(args.duration)
    elif args.pattern == "sine":
        cmds = generate_sine_commands(args.duration)
    else:
        cmds = generate_sawtooth_noise_commands(
            args.duration, noise_sigma=args.noise_sigma
        )

    print(f"Pattern: {args.pattern}, Duration: {args.duration}s, Commands: {len(cmds)}")
    print(f"iperf server: h1 iperf -s -u &")
    print(f"---")
    for t, bw in cmds:
        print(f"t={t:>4d}s → h3 iperf -c 10.0.0.1 -u -b {bw}M -t 3 -i 1")
