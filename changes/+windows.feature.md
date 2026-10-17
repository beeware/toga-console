The console has a Windows backend, built on the Winforms `RichTextBox`. On Windows, `attach` reads a pipe on a helper thread, because Toga's Windows event loop cannot watch a descriptor.
