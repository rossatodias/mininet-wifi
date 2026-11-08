#!/usr/bin/env python3

'Configurable UAV experiments with Mininet-WiFi and CoppeliaSim'

from experiment import config, runner


if __name__ == '__main__':
    runner.run(config.parse_args())
