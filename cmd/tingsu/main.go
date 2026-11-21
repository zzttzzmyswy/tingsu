package main

import (
	"fmt"
	"os"

	"github.com/zzttzzmyswy/tingsu/internal/bundle"
	"github.com/zzttzzmyswy/tingsu/internal/desktop"
)

func main() {
	err := bundle.Prepare()
	if err == nil {
		err = desktop.Run(os.Args[1:])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "听序:", err)
		if len(os.Args) == 1 {
			showStartupError(err)
		}
		os.Exit(1)
	}
}
