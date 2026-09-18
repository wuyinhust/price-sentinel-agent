// Vision OCR helper for the screenshot-based collection channel.
//
// Prints "text<TAB>x<TAB>y<TAB>w<TAB>h" per recognised line. Coordinates are
// normalized 0-1 with the origin at the BOTTOM-LEFT, which is what Vision
// reports; callers flip y when converting to screen pixels.
//
// Why Objective-C and not Swift: this machine's Swift compiler (5.4.2) is older
// than its SDK (MacOSX12.1.sdk), so building against the Swift module interface
// fails with "may have used features that aren't supported by this compiler".
// clang has no such mismatch. Build with tools/ocr/build.sh, which picks an SDK
// that matches the running system.
#import <AppKit/AppKit.h>
#import <Foundation/Foundation.h>
#import <Vision/Vision.h>

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        if (argc < 2) {
            fprintf(stderr, "usage: ocr <image>\n");
            return 2;
        }
        NSString *path = [NSString stringWithUTF8String:argv[1]];
        NSImage *image = [[NSImage alloc] initWithContentsOfFile:path];
        if (!image) {
            fprintf(stderr, "cannot load image: %s\n", argv[1]);
            return 2;
        }
        CGImageRef cgImage = [image CGImageForProposedRect:NULL context:nil hints:nil];
        if (!cgImage) {
            fprintf(stderr, "cannot decode image\n");
            return 2;
        }

        VNRecognizeTextRequest *request = [[VNRecognizeTextRequest alloc] init];
        request.recognitionLevel = VNRequestTextRecognitionLevelAccurate;
        // zh-Hans is what makes Chinese product titles readable; without it
        // Vision returns garbage on CJK glyphs.
        request.recognitionLanguages = @[ @"zh-Hans", @"en-US" ];
        // Off on purpose. Language correction "fixes" model numbers and prices
        // into more plausible ones -- measured on this machine, a strikethrough
        // ¥288 came back as ¥233. That is exactly the data we must not corrupt.
        request.usesLanguageCorrection = NO;

        VNImageRequestHandler *handler =
            [[VNImageRequestHandler alloc] initWithCGImage:cgImage options:@{}];
        NSError *error = nil;
        if (![handler performRequests:@[ request ] error:&error]) {
            fprintf(stderr, "ocr failed: %s\n", error.localizedDescription.UTF8String);
            return 1;
        }

        for (VNRecognizedTextObservation *observation in request.results) {
            VNRecognizedText *text = [observation topCandidates:1].firstObject;
            if (!text) {
                continue;
            }
            CGRect box = observation.boundingBox;
            NSString *clean =
                [text.string stringByReplacingOccurrencesOfString:@"\t" withString:@" "];
            printf("%s\t%f\t%f\t%f\t%f\n", clean.UTF8String, box.origin.x, box.origin.y,
                   box.size.width, box.size.height);
        }
    }
    return 0;
}
