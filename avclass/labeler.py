import argparse
import os
import json
import sys
import traceback

from operator import itemgetter
from typing import AnyStr, Optional

from avclass.common import AvLabels, Taxonomy
from avclass import clustering as ec, util


def guess_hash(h: AnyStr) -> Optional[AnyStr]:
    """
    Guess hash type based on ``len(h)``

    :param h: The hash
    :return: The hash type (str)
    """
    ''' Given a hash string, guess the hash type based on the string length '''
    hlen = len(h)
    if hlen == 32:
        return 'md5'
    elif hlen == 40:
        return 'sha1'
    elif hlen == 64:
        return 'sha256'

    return None


def format_tag_pairs(l, taxonomy: Taxonomy = None) -> AnyStr:
    """
    Get ranked tags as a string.

    :param l:
    :param taxonomy:
    :return:
    """
    # TODO - wtf is ``l``?
    if not l:
        return ""

    if taxonomy is not None:
        p = taxonomy.get_path(l[0][0])
    else:
        p = l[0][0]

    out = "%s|%d" % (p, l[0][1])
    for t, s in l[1:]:
        if taxonomy is not None:
            p = taxonomy.get_path(t) 
        else:
            p = t
        out += ",%s|%d" % (p, s)

    return out


def list_str(l, sep: AnyStr = ", ", prefix: AnyStr = "") -> AnyStr:
    """
    Return list as a string

    :param l: The list
    :param sep: The separator
    :param prefix: The prefix
    :return: A string representation of the list
    """
    # TODO - wtf is ``l``?
    if not l:
        return ""
    out = prefix + l[0]
    for s in l[1:]:
        out = out + sep + s
    return out


def main():
    # TODO - break this function up.
    args = parse_args()
    # Select hash used to identify sample, by default MD5
    hash_type = args.hash or 'md5'

    # If ground truth provided, read it from file
    gt_dict = {}
    if args.gt:
        with open(args.gt, 'r') as gt_fd:
            for line in gt_fd:
                gt_hash, family = map(str, line.strip().split('\t', 1))
                gt_dict[gt_hash] = family

        # Guess type of hash in ground truth file
        hash_type = guess_hash(list(gt_dict.keys())[0])

    # Create AvLabels object
    av_labels = AvLabels(args.tag, args.exp, args.tax, args.av, args.aliasdetect)

    # Build list of input files
    # NOTE: duplicate input files are not removed
    ifile_l = []
    if args.vt:
        ifile_l += args.vt
        ifile_are_vt = True
    elif args.lb:
        ifile_l += args.lb
        ifile_are_vt = False
    elif args.vtdir:
        ifile_l += [os.path.join(args.vtdir, f) for f in os.listdir(args.vtdir)]
        ifile_are_vt = True
    elif args.lbdir:
        ifile_l += [os.path.join(args.lbdir, f) for f in os.listdir(args.lbdir)]
        ifile_are_vt = False
    else:
        # TODO - is this reachable?
        sys.exit(1)

    # Select correct sample info extraction function
    if not ifile_are_vt:
        get_sample_info = av_labels.get_sample_info_lb
    elif args.vt3:
        get_sample_info = av_labels.get_sample_info_vt_v3
    else:
        get_sample_info = av_labels.get_sample_info_vt_v2

    # Select output prefix
    out_prefix = os.path.basename(os.path.splitext(ifile_l[0])[0])

    # Initialize state
    first_token_dict = {}
    token_count_map = {}
    pair_count_map = {}
    vt_all = 0
    avtags_dict = {}
    stats = {'samples': 0, 'noscans': 0, 'tagged': 0, 'maltagged': 0,
             'FAM': 0, 'CLASS': 0, 'BEH': 0, 'FILE': 0, 'UNK': 0}

    for ifile in ifile_l:
        fd = open(ifile, 'r')
        sys.stderr.write('[-] Processing input file %s\n' % ifile)

        for line in fd:
            if not line.strip():
                continue

            # Debug info
            if vt_all % 100 == 0:
                sys.stderr.write('\r[-] %d JSON read' % vt_all)
                sys.stderr.flush()
            vt_all += 1

            vt_rep = json.loads(line)
            sample_info = get_sample_info(vt_rep)

            if sample_info is None:
                try:
                    name = vt_rep['md5']
                    sys.stderr.write('\nNo scans for %s\n' % name)
                except KeyError:
                    sys.stderr.write('\nCould not process: %s\n' % line)

                sys.stderr.flush()
                stats['noscans'] += 1
                continue

            # Sample's name is selected hash type (md5 by default)
            name = getattr(sample_info, hash_type)

            # If the VT report has no AV labels, output and continue
            if not sample_info.labels:
                sys.stdout.write('%s\t-\t[]\n' % name)
                # sys.stderr.write('\nNo AV labels for %s\n' % name)
                # sys.stderr.flush()
                continue

            # Compute VT_Count
            vt_count = len(sample_info.labels)

            # Get the distinct tokens from all the av labels in the report and print them.
            try:
                av_tmp = av_labels.get_sample_tags(sample_info)
                tags = av_labels.rank_tags(av_tmp)

                # AV VENDORS PER TOKEN
                if args.avtags:
                    for t in av_tmp:
                        tmap = avtags_dict.get(t, {})
                        for av in av_tmp[t]:
                            ctr = tmap.get(av, 0)
                            tmap[av] = ctr + 1
                        avtags_dict[t] = tmap

                if args.aliasdetect:
                    prev_tokens = set()
                    for entry in tags:
                        curr_tok = entry[0]
                        curr_count = token_count_map.get(curr_tok, 0)
                        token_count_map[curr_tok] = curr_count + 1
                        for prev_tok in prev_tokens:
                            if prev_tok < curr_tok:
                                pair = prev_tok, curr_tok
                            else:
                                pair = curr_tok, prev_tok
                            pair_count = pair_count_map.get(pair, 0)
                            pair_count_map[pair] = pair_count + 1
                        prev_tokens.add(curr_tok)

                # Collect stats
                # TODO - should iterate once over tags for both stats and aliasdetect
                if tags:
                    stats["tagged"] += 1
                    if args.stats:
                        if vt_count > 3:
                            stats["maltagged"] += 1
                            cat_map = {'FAM': False, 'CLASS': False, 'BEH': False, 'FILE': False, 'UNK': False}
                            for t in tags:
                                path, cat = av_labels.taxonomy.get_info(t[0])
                                cat_map[cat] = True
                            for c in cat_map:
                                if cat_map[c]:
                                    stats[c] += 1

                # Check if sample is PUP, if requested
                if args.pup:
                    if av_labels.is_pup(tags, av_labels.taxonomy):
                        is_pup_str = "\t1"
                    else:
                        is_pup_str = "\t0"
                else:
                    is_pup_str = ""

                # Select family for sample if needed,
                # i.e., for compatibility mode or for ground truth
                fam = "SINGLETON:" + name
                if args.c or args.gt:
                    for t, s in tags:
                        cat = av_labels.taxonomy.get_category(t)
                        if cat in ["UNK", "FAM"]:
                            fam = t
                            break

                    first_token_dict[name] = fam
                    gt_family = '\t' + gt_dict.get(name, "")
                else:
                    gt_family = ""

                # Get VT tags as string
                if args.vtt:
                    vtt = list_str(sample_info.vt_tags, prefix="\t")
                else:
                    vtt = ""

                # Print family (and ground truth if available) to stdout
                if not args.c:
                    if args.path:
                        tag_str = format_tag_pairs(tags, av_labels.taxonomy)
                    else:
                        tag_str = format_tag_pairs(tags)
                    sys.stdout.write('%s\t%d\t%s%s%s%s\n' % name, vt_count, tag_str, gt_family, is_pup_str, vtt)
                else:
                    sys.stdout.write('%s\t%s%s%s\n' % name, fam, gt_family, is_pup_str)
            except:
                traceback.print_exc(file=sys.stderr)
                continue

        sys.stderr.write('\r[-] %d JSON read' % vt_all)
        sys.stderr.flush()
        sys.stderr.write('\n')

        fd.close()

    # Print statistics
    sys.stderr.write("[-] Samples: %d NoScans: %d NoTags: %d GroundTruth: %d\n" %
                     (vt_all, stats['noscans'], vt_all - stats['tagged'], len(gt_dict)))

    # If ground truth, print precision, recall, and F1-measure
    if args.gt:
        precision, recall, fmeasure = ec.eval_precision_recall_fmeasure(gt_dict, first_token_dict)
        sys.stderr.write("Precision: %.2f\tRecall: %.2f\tF1-Measure: %.2f\n" % (precision, recall, fmeasure))

    # Output stats
    if args.stats:
        stats_fd = open("%s.stats" % out_prefix, 'w')
        num_samples = vt_all
        stats_fd.write('Samples: %d\n' % num_samples)
        num_tagged = stats['tagged']
        frac = float(num_tagged) / float(num_samples) * 100
        stats_fd.write('Tagged (all): %d (%.01f%%)\n' % (num_tagged, frac))
        num_maltagged = stats['maltagged']
        frac = float(num_maltagged) / float(num_samples) * 100
        stats_fd.write('Tagged (VT>3): %d (%.01f%%)\n' % (num_maltagged, frac))
        for c in ['FILE', 'CLASS', 'BEH', 'FAM', 'UNK']:
            count = stats[c]
            frac = float(count) / float(num_maltagged) * 100
            stats_fd.write('%s: %d (%.01f%%)\n' % (c, stats[c], frac))
        stats_fd.close()

    # Output vendor info
    if args.avtags:
        avtags_fd = open("%s.avtags" % out_prefix, 'w')
        for t in sorted(avtags_dict.keys()):
            avtags_fd.write('%s\t' % t)
            pairs = sorted(avtags_dict[t].items(), key=lambda pair: pair[1], reverse=True)

            for pair in pairs:
                avtags_fd.write('%s|%d,' % (pair[0], pair[1]))
            avtags_fd.write('\n')
        avtags_fd.close()

    # If alias detection, print map
    if args.aliasdetect:
        alias_filename = out_prefix + '.alias'
        alias_fd = open(alias_filename, 'w+')
        # Sort token pairs by number of times they appear together
        sorted_pairs = sorted(
            pair_count_map.items(), key=itemgetter(1))
        # sorted_pairs = sorted(
        #     pair_count_map.items())

        # Output header line
        alias_fd.write("# t1\tt2\t|t1|\t|t2|\t|t1^t2|\t|t1^t2|/|t1|\t|t1^t2|/|t2|\n")
        # Compute token pair statistic and output to alias file
        for t1, t2, c in sorted_pairs:
            n1 = token_count_map[t1]
            n2 = token_count_map[t2]
            if n1 < n2:
                x = t1
                y = t2
                xn = n1
                yn = n2
            else:
                x = t2
                y = t1
                xn = n2
                yn = n1
            f = float(c) / float(xn)
            finv = float(c) / float(yn)
            alias_fd.write("%s\t%s\t%d\t%d\t%d\t%0.2f\t%0.2f\n" % (x, y, xn, yn, c, f, finv))
        # Close alias file
        alias_fd.close()
        sys.stderr.write('[-] Alias data in %s\n' % alias_filename)


def parse_args():
    argparser = argparse.ArgumentParser(prog='avclass',
                                        description='Extracts tags for a set of samples.  Also calculates precision and'
                                                    ' recall if ground truth available')

    argparser.add_argument('-vt', action='append', help='file with VT reports (Can be provided multiple times)')

    argparser.add_argument('-lb', action='append', help='file with simplified JSON reports '
                                                        '{md5,sha1,sha256,scan_date,av_labels} (Can be provided '
                                                        'multiple times)')

    argparser.add_argument('-vtdir', help='existing directory with VT reports')

    argparser.add_argument('-lbdir', help='existing directory with simplified JSON reports')

    argparser.add_argument('-vt3', action='store_true', help='input are VT v3 files')

    argparser.add_argument('-gt', help='file with ground truth. If provided it evaluates clustering accuracy. '
                                       'Prints precision, recall, F1-measure.')

    argparser.add_argument('-vtt', help='Include VT tags in the output.', action='store_true')

    argparser.add_argument('-tag', help='file with tagging rules.', default=util.DEFAULT_TAG_PATH)

    argparser.add_argument('-tax', help='file with taxonomy.', default=util.DEFAULT_TAX_PATH)

    argparser.add_argument('-exp', help='file with expansion rules.', default=util.DEFAULT_EXP_PATH)

    argparser.add_argument('-av', help='file with list of AVs to use')

    argparser.add_argument('-avtags', help='extracts tags per av vendor', action='store_true')

    argparser.add_argument('-pup', action='store_true', help='if used each sample is classified as PUP or not')

    argparser.add_argument('-p', '--path', help='output.full path for tags', action='store_true')

    argparser.add_argument('-hash', help='hash used to name samples. Should match ground truth',
                           choices=['md5', 'sha1', 'sha256'])

    argparser.add_argument('-c', help='Compatibility mode. Outputs results in AVClass format.', action='store_true')

    argparser.add_argument('-aliasdetect', action='store_true', help='if used produce aliases file at end')

    argparser.add_argument('-stats', action='store_true', help='if used produce 1 file with stats per category '
                                                               '(File, Class, Behavior, Family, Unclassified)')

    args = argparser.parse_args()

    # TODO - use non-exclusive group to ensure at least one is selected instead of this
    if not args.vt and not args.lb and not args.vtdir and not args.lbdir:
        sys.stderr.write('One of the following 4 arguments is required: '
                         '-vt,-lb,-vtdir,-lbdir\n')
        exit(1)

    # TODO - use mutex group for this instead of manual check
    if (args.vt or args.vtdir) and (args.lb or args.lbdir):
        sys.stderr.write('Use either -vt/-vtdir or -lb/-lbdir. '
                         'Both types of input files cannot be combined.\n')
        exit(1)

    # TODO - consider letting argparse handle this?
    if args.tag:
        if args.tag == '/dev/null':
            sys.stderr.write('[-] Using no tagging rules\n')
        else:
            sys.stderr.write('[-] Using tagging rules in %s\n' % args.tag)
    else:
        sys.stderr.write('[-] Using default tagging rules in %s\n' % util.DEFAULT_TAG_PATH)

    # TODO - consider letting argparse handle this?
    if args.tax:
        if args.tax == '/dev/null':
            sys.stderr.write('[-] Using no taxonomy\n')
        else:
            sys.stderr.write('[-] Using taxonomy in %s\n' % args.tax)
    else:
        sys.stderr.write('[-] Using default taxonomy in %s\n' % util.DEFAULT_TAX_PATH)

    # TODO - consider letting argparse handle this?
    if args.exp:
        if args.exp == '/dev/null':
            sys.stderr.write('[-] Using no expansion tags\n')
        else:
            sys.stderr.write('[-] Using expansion tags in %s\n' % args.exp)
    else:
        sys.stderr.write('[-] Using default expansion tags in %s\n' % util.DEFAULT_EXP_PATH)

    return args


if __name__ == '__main__':
    main()
