package main

import (
	"fmt"
	"github.com/zzttzzmyswy/tingsu/internal/bundle"
	"github.com/zzttzzmyswy/tingsu/internal/desktop"
	"os"
)

func main() {
	err := bundle.Prepare()
	if err == nil {
		err = desktop.Run(os.Args[1:])
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "听序:", err)
		os.Exit(1)
	}
}
