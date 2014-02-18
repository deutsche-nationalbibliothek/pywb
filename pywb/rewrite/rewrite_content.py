import chardet

from url_rewriter import UrlRewriter
from html_rewriter import HTMLRewriter
from regex_rewriters import RegexRewriter, JSRewriter, CSSRewriter, XMLRewriter
from header_rewriter import HeaderRewriter, RewrittenStatusAndHeaders

from pywb.utils.statusandheaders import StatusAndHeaders
from pywb.utils.bufferedreaders import BufferedReader, ChunkedDataReader

class RewriteContent:

    DEFAULT_CONTENT_REWRITERS = {
      'header': HeaderRewriter,
      'js': JSRewriter,
      'css': CSSRewriter,
      'xml': XMLRewriter,
      'html': HTMLRewriter
    }


    def __init__(self, rewriters = {}):
        self.rewriters = dict(self.DEFAULT_CONTENT_REWRITERS.items() + rewriters.items())


    def rewrite_headers(self, urlrewriter, status_headers, stream):
        rewritten_headers = self.rewriters['header']().rewrite(status_headers, urlrewriter)

        # note: since chunking may be broken, approach taken here is to *always* attempt
        # to dechunk if transfer-encoding: chunked is present
        #
        # an alternative may be to serve chunked unless content rewriting is needed
        # todo: possible revisit this approach

        if (rewritten_headers.contains_removed_header('transfer-encoding', 'chunked')):
            stream = ChunkedDataReader(stream)

        return (rewritten_headers, stream)

    def rewrite_content(self, urlrewriter, headers, stream, head_insert_str = None):
        # see if we've already rewritten headers
        if isinstance(headers, RewrittenStatusAndHeaders):
            rewritten_headers = headers
        elif isinstance(headers, StatusAndHeaders):
        # otherwise, need to determine if rewriting is even necessary
            (rewritten_headers, stream) = self.rewrite_headers(urlrewriter, headers, stream)
            # no rewriting needed here
            if rewritten_headers.text_type is None:
                gen = self.stream_to_gen(stream)
                return (status_headers, gen)

        status_headers = rewritten_headers.status_headers
        # Handle text content rewriting
        # =========================================================================
        # special case -- need to ungzip the body
        if (rewritten_headers.contains_removed_header('content-encoding', 'gzip')):
            stream = BufferedReader(stream, decomp_type='gzip')

        if rewritten_headers.charset:
            encoding = rewritten_headers.charset
            first_buff = None
        else:
            (encoding, first_buff) = self._detect_charset(stream)

            # if chardet thinks its ascii, use utf-8
            if encoding == 'ascii':
                encoding = 'utf-8'

        text_type = rewritten_headers.text_type

        rewriter_class = self.rewriters.get(text_type)
        if not rewriter_class:
            raise Exception('Unknown Text Type for Rewrite: ' + text_type)


        if text_type == 'html':
            rewriter = rewriter_class(urlrewriter, outstream = None, head_insert = head_insert_str)
        else:
            rewriter = rewriter_class(urlrewriter)

        # Create rewriting generator
        gen = self._rewriting_stream_gen(rewriter, encoding, stream, first_buff)
        return (status_headers, gen)


    # Create rewrite stream,  may even be chunked by front-end
    def _rewriting_stream_gen(self, rewriter, encoding, stream, first_buff = None):
        def do_rewrite(buff):
            if encoding:
                buff = self._decode_buff(buff, stream, encoding)

            buff = rewriter.rewrite(buff)

            if encoding:
                buff = buff.encode(encoding)

            return buff

        def do_finish():
            return rewriter.close()

        return self.stream_to_gen(stream, rewrite_func = do_rewrite, final_read_func = do_finish, first_buff = first_buff)


    def _decode_buff(self, buff, stream, encoding):
        try:
            buff = buff.decode(encoding)
        except UnicodeDecodeError, e:
            # chunk may have cut apart unicode bytes -- add 1-3 bytes and retry
            for i in range(3):
                buff += stream.read(1)
                try:
                    buff = buff.decode(encoding)
                    break
                except UnicodeDecodeError:
                    pass
            else:
                raise

        return buff


    def _detect_charset(self, stream):
        buff = stream.read(8192)
        result = chardet.detect(buff)
        print "chardet result: " + str(result)
        return (result['encoding'], buff)


    # Create a generator reading from a stream, with optional rewriting and final read call
    @staticmethod
    def stream_to_gen(stream, rewrite_func = None, final_read_func = None, first_buff = None):
        try:
            buff = first_buff if first_buff else stream.read()
            while buff:
                if rewrite_func:
                    buff = rewrite_func(buff)
                yield buff
                buff = stream.read()

            # For adding a tail/handling final buffer
            if final_read_func:
                buff = final_read_func()
                if buff:
                    yield buff

        finally:
            stream.close()


