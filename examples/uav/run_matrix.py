#!/usr/bin/python

'Run a matrix of UAV experiments and aggregate the results'

import sys

from experiment import matrix


if __name__ == '__main__':
    sys.exit(matrix.main())
