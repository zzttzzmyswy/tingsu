//go:build !windows

package main

// On other platforms main reports the error on stderr.
func showStartupError(error) {}
